#!/usr/bin/env python3
"""يشغّل استئصالَ مكوّنٍ واحدٍ من بروتوكول ك٤٦ بذراعيه، ويكتب التقريرَ والقرار.

    python3 tools/evaluate_ablation.py --component tool_announcement --model qwen3.5:9b \\
        --sample-target 600 --out docs/probe/k46-tool-announcement-<التاريخ>.json
    python3 tools/evaluate_ablation.py --component search --model qwen3.5:9b \\
        --out docs/probe/k46-search-<التاريخ>.json

- **الحالات:** حالاتُ الشطر المفتوح من بنك Kimi ذاتُ الفحص الآليّ (الطبقات أ–ج)، أو عيّنةٌ طبقيّةٌ منها
  بـ`--sample-target` (`tools/sample_bank.py`، الحرِجُ كلُّه يدخل).
  - وبلا حاويةِ فحصٍ تخرج حالاتُ python_sandbox بـ`--no-sandbox`، ويُعلَن ذلك في الإعداد.
- **البحث:** يشغّل بنكَ ك٥٣ مرّتين، بأداة البحث وبلاها.
- **الحدّ:** مكوّنٌ معطَّلٌ في البروتوكول (المتّجهات، وتوسيعُ CAMeL) يُرفض بالاسم. والتقريرُ لا يُكتب فوق ملفٍّ قائم.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.ablation import (GREEDY_SEED_LIMIT, RUNNER_VERSION, AblationError, RunCache, _sha,  # noqa: E402
                                 aggregate_seed_rows, arm, auto_checked, judge, protocol, run_seeded_arm, seed_values)
from tools.model_digest import ModelDigestError, pin_model_digest, verify_model_digest  # noqa: E402
from tools.sample_bank import load_capability_suites, stratified  # noqa: E402


def bank_cases(bank_open: Path, *, sample_target: int | None, salt: str, sandbox: bool) -> tuple[list, list]:
    suites = load_capability_suites(bank_open)          # المهامُّ الوكيلة (الطبقة د) لها مُشغِّلُها
    files = [{"path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
              "sha256": _sha(path.read_bytes())} for path, _ in suites]
    # المؤهَّلُ يُصفّى قبل السحب لا بعده: كانت العيّنةُ تُسحب من البنك كلِّه ثم يسقط منها ما لا فحصَ آليًّا له، فنقصت عن
    # الهدف (٦٢٨ ← ٤٥٧ والمؤهَّلُ ١٣٧٣) واختلّ مزيجُ القدرات، فصار «ناقصُ القوة» أثرَ الترتيب لا قلّةَ الحالات (#185)
    eligible = [(path, {**suite, "cases": [case for case in suite["cases"] if auto_checked(case, sandbox=sandbox)]})
                for path, suite in suites]
    if sample_target is None:
        cases = [case for _, suite in eligible for case in suite["cases"]]
    else:
        cases = [case for group in stratified(eligible, sample_target, salt).values() for case in group]
    return cases, files


def run_component(component: str, provider, *, model: str, model_version: str, bank_open: Path,
                  sample_target: int | None = None, salt: str = "k46", sandbox: bool = True,
                  seeds: tuple[int, ...] | None = None, checkpoint: Path | None = None,
                  on_checkpoint_opened=None, **options) -> dict:
    spec = protocol()["components"].get(component)
    if spec is None:
        raise AblationError("component_unknown", component)
    if spec["status"] != "ready":
        raise AblationError("component_blocked", ", ".join(spec.get("blocked_by", [])))
    seeds = seed_values() if seeds is None else tuple(seeds)
    if seeds != seed_values(len(seeds)):
        raise AblationError("seeds_invalid", "يلزم تسلسل 0..N-1 بعدد فردي لا يقل عن 3")
    # مخبأُ الاستئناف لطريق الحلقة وحده: مُشغِّلُ البحث يقيس بنكَه دفعةً واحدة (ثلاثون سؤالًا) فلا يحتاجه، ولا يُقبل له مخبأٌ
    # لا يستعمله، فلا يُحجَر ملفٌّ لم يُقرأ ولم يُكتب في هذا التشغيل (ملاحظة Codex على #351)
    if checkpoint is not None and spec["runner"] == "research":
        raise AblationError("checkpoint_unused_by_research_runner", component)
    cache = RunCache(checkpoint) if checkpoint is not None else None
    if cache is not None and on_checkpoint_opened is not None:
        on_checkpoint_opened(cache)
    rows_in_file = len(cache.rows) if cache is not None else 0
    config = {"runner_version": RUNNER_VERSION, "component": component, "model": model,
              "model_version": model_version, "arms": spec["arms"], "options": dict(options),
              "seeds": list(seeds), "seed_aggregation": protocol()["seed_aggregation"], "checkpoint": None}
    if spec["runner"] == "research":
        from evaluation.research_runner import RUNNER_VERSION as RESEARCH_RUNNER_VERSION, run_bank
        config["research_runner_version"] = RESEARCH_RUNNER_VERSION
        rows = {}
        for side in ("on", "off"):
            runs = []
            for seed in seeds:
                if not hasattr(provider, "with_seed"):
                    raise AblationError("provider_seed_unsupported", getattr(provider, "name", type(provider).__name__))
                report = run_bank(provider.with_seed(seed), model=model, model_version=model_version,
                                  **options, **spec["arms"][side])
                runs.append((seed, report["results"]))
            rows[side] = aggregate_seed_rows(runs)
        config["bank"] = "evaluation/suites/research_v1.json"
    else:
        cases, files = bank_cases(bank_open, sample_target=sample_target, salt=salt, sandbox=sandbox)
        config.update(bank={"files": files, "sample_target": sample_target, "salt": salt,
                            "sandbox_cases_included": sandbox, "cases": len(cases)})
        rows = {side: run_seeded_arm(cases, provider, arm(spec["arms"][side]), seeds, cache, model=model,
                                     model_version=model_version, **options) for side in ("on", "off")}
    if cache is not None:
        # يُعدّ ما استُعمل من الملف فعلًا بعد القياس، لا مفاتيحُه قبله: فصفوفُ بصمةٍ أو حالاتٍ أخرى ليست استعادة (ملاحظة Codex على #192)
        config["checkpoint"] = {"path": str(checkpoint), "rows_in_file_at_start": rows_in_file,
                                "measured_rows_reused": cache.reused,
                                "partial_tail_dropped": cache.dropped_partial_tail}
    return {"schema_version": 2, "kind": "ablation_report", "config": config, "arms": rows,
            "judgment": judge(component, rows["on"], rows["off"]),
            "measurement_limits": protocol()["limits"] + [GREEDY_SEED_LIMIT] + spec["limits"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--component", required=True, choices=sorted(protocol()["components"]))
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-version", help="بصمةُ النموذج المتوقَّعة؛ تُقارَن بما يعرضه Ollama")
    parser.add_argument("--bank-open", type=Path, default=ROOT / "evaluation/banks/kimi_v1/open")
    parser.add_argument("--sample-target", type=int)
    parser.add_argument("--seeds", type=int, default=3,
                        help="عددٌ فردي من البذور (الافتراضي 3؛ تُستخدم 0..N-1)")
    parser.add_argument("--salt", default="k46")
    parser.add_argument("--no-sandbox", action="store_true", help="تُستبعد حالاتُ python_sandbox حيث لا حاويةَ فحص")
    parser.add_argument("--checkpoint", type=Path,
                        help="ملفُّ JSONL خارج docs/ يُكتب إليه كلُّ صفٍّ مقيس فور قياسه؛ وإعادةُ الأمر نفسِه تستأنف منه")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(f"التقريرُ قائم: {args.out}")
    try:
        seeds = seed_values(args.seeds)
    except AblationError as exc:
        print(json.dumps({"status": "refused", "code": exc.code}, ensure_ascii=False))
        return 2
    from providers.ollama import OllamaProvider
    try:
        model_version = pin_model_digest(args.model, args.model_version)
    except ModelDigestError as exc:
        print(json.dumps({"status": "refused", "code": exc.code}, ensure_ascii=False))
        return 1
    failure: BaseException | None = None
    opened: list = []       # المخبأُ الذي فتحه هذا التشغيلُ فعلًا، وحده يُحجَر (ملاحظة Codex على #351)
    try:
        report = run_component(args.component, OllamaProvider(args.model), model=args.model,
                               model_version=model_version, bank_open=args.bank_open,
                               sample_target=args.sample_target, salt=args.salt, sandbox=not args.no_sandbox,
                               seeds=seeds, checkpoint=args.checkpoint, on_checkpoint_opened=opened.append)
    except BaseException as exc:          # يُعاد التحقّقُ من البصمة على كلِّ خروجٍ بعد بدء القياس، ولو مقاطعةً (ملاحظة Codex على #192)
        failure = exc
    try:
        verify_model_digest(args.model, model_version)
    except ModelDigestError as exc:
        # صفوفُ المخبأ كُتبت قبل هذا التحقّق منسوبةً إلى البصمة المثبّتة؛ فإن انحرف الوسمُ أثناء الليلة فهي لا تصلح استئنافًا
        # ولو عاد الوسمُ إلى بصمته: يُنقل الملفُّ إلى اسمٍ لا يُقرأ منه، ويُعلَن (ملاحظة Codex الأولى على #192)
        quarantined = RunCache.quarantine_file(args.checkpoint) if opened else None
        print(json.dumps({"status": "refused", "code": exc.code,
                          "checkpoint_quarantined": str(quarantined) if quarantined is not None else None},
                         ensure_ascii=False))
        return 1
    if isinstance(failure, AblationError):
        print(json.dumps({"status": "refused", "code": failure.code}, ensure_ascii=False))
        return 2
    if failure is not None:
        raise failure
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    verdict = report["judgment"]
    print(json.dumps({"component": args.component, "decision": verdict["decision"], "reason": verdict["reason"],
                      "overall": verdict["overall"], "out": str(args.out)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
