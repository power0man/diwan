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

from evaluation.ablation import (RUNNER_VERSION, AblationError, _sha, arm, auto_checked, judge,  # noqa: E402
                                 protocol, run_arm)
from tools.sample_bank import load_capability_suites, stratified  # noqa: E402


def bank_cases(bank_open: Path, *, sample_target: int | None, salt: str, sandbox: bool) -> tuple[list, list]:
    suites = load_capability_suites(bank_open)          # المهامُّ الوكيلة (الطبقة د) لها مُشغِّلُها
    files = [{"path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
              "sha256": _sha(path.read_bytes())} for path, _ in suites]
    if sample_target is None:
        cases = [case for _, suite in suites for case in suite["cases"]]
    else:
        cases = [case for group in stratified(suites, sample_target, salt).values() for case in group]
    return [case for case in cases if auto_checked(case, sandbox=sandbox)], files


def run_component(component: str, provider, *, model: str, model_version: str, bank_open: Path,
                  sample_target: int | None = None, salt: str = "k46", sandbox: bool = True, **options) -> dict:
    spec = protocol()["components"].get(component)
    if spec is None:
        raise AblationError("component_unknown", component)
    if spec["status"] != "ready":
        raise AblationError("component_blocked", ", ".join(spec.get("blocked_by", [])))
    config = {"runner_version": RUNNER_VERSION, "component": component, "model": model,
              "model_version": model_version, "arms": spec["arms"], "options": dict(options)}
    if spec["runner"] == "research":
        from evaluation.research_runner import run_bank
        rows = {side: run_bank(provider, model=model, model_version=model_version, **options,
                               **spec["arms"][side])["results"] for side in ("on", "off")}
        config["bank"] = "evaluation/suites/research_v1.json"
    else:
        cases, files = bank_cases(bank_open, sample_target=sample_target, salt=salt, sandbox=sandbox)
        config.update(bank={"files": files, "sample_target": sample_target, "salt": salt,
                            "sandbox_cases_included": sandbox, "cases": len(cases)})
        rows = {side: run_arm(cases, provider, arm(spec["arms"][side]), model=model,
                              model_version=model_version, **options) for side in ("on", "off")}
    return {"schema_version": 1, "kind": "ablation_report", "config": config, "arms": rows,
            "judgment": judge(component, rows["on"], rows["off"]),
            "measurement_limits": protocol()["limits"] + spec["limits"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--component", required=True, choices=sorted(protocol()["components"]))
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-version", default="unspecified")
    parser.add_argument("--bank-open", type=Path, default=ROOT / "evaluation/banks/kimi_v1/open")
    parser.add_argument("--sample-target", type=int)
    parser.add_argument("--salt", default="k46")
    parser.add_argument("--no-sandbox", action="store_true", help="تُستبعد حالاتُ python_sandbox حيث لا حاويةَ فحص")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(f"التقريرُ قائم: {args.out}")
    from providers.ollama import OllamaProvider
    try:
        report = run_component(args.component, OllamaProvider(args.model), model=args.model,
                               model_version=args.model_version, bank_open=args.bank_open,
                               sample_target=args.sample_target, salt=args.salt, sandbox=not args.no_sandbox)
    except AblationError as exc:
        print(json.dumps({"status": "refused", "code": exc.code}, ensure_ascii=False))
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    verdict = report["judgment"]
    print(json.dumps({"component": args.component, "decision": verdict["decision"], "reason": verdict["reason"],
                      "overall": verdict["overall"], "out": str(args.out)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
