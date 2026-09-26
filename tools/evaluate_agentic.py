#!/usr/bin/env python3
"""يشغّل بنكَ المهامّ الوكيلة ويكتب تقريرَه — الحكمُ على حالة العالَم.

الطريقُ الأوّل (ج٣): أمرُ النجاح يجري في حاويةٍ زائلة بإيصال Docker المعتمَد،
فلا تُنفَّذ شيفرةٌ كتبها النموذج على الجهاز:

    python3 tools/evaluate_agentic.py --suite evaluation/suites/agentic_v1.json \
        --model qwen3:14b --execution-receipt <مسارُ الإيصال الخاص> \
        --out docs/probe/agentic-<تاريخ>.json

وبنكُ المحلّل (ك٥٠) يُشغَّل مع صورة المحلّل (ج٨): `--analysis-receipt <إيصالُها>`
فتُعلَن `analyze_data` وتُضبط صورتُها لمساحة كل مهمّة.

وبلا إيصالٍ يبقى الطريقُ القديم على المضيف، ولا يُقبل إلا بإقرار مضيفٍ زائل (ق٤٤):

    DIWAN_DISPOSABLE_HOST=<اسمُ المضيف> python3 tools/evaluate_agentic.py ...

والإقرارُ إقرارُ مشغِّلٍ لا حدٌّ متحقَّقٌ منه — ولذلك يُسجَّل في الإعداد وتُنشر
حدودُه مع الرقم. والتقريرُ يقول أين جرى الأمر: `config.success_executor`.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.builtin_tools import DEFAULT_TOOLS
from agent.registry import ToolRegistry
from core.canonical import PayloadRejected
from evaluation.agentic_bank import attach as attach_thresholds
from evaluation.agentic_runner import run_agentic_suite


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path,
                        default=ROOT / "evaluation/suites/agentic_v1.json")
    parser.add_argument("--model", required=True, help="اسمُ النموذج كما يعرفه المزوّد")
    parser.add_argument("--model-version", default="unspecified")
    parser.add_argument("--out", type=Path, help="مسارُ التقرير؛ لا يُستبدل ملفٌّ قائم")
    parser.add_argument("--max-output", type=int, default=1024)
    parser.add_argument("--deadline-s", type=float, default=120.0)
    parser.add_argument("--execution-receipt", type=Path,
                        help="إيصالُ صورة Docker الخاص (ج٣): يُشغَّل أمرُ النجاح في الحاوية لا على الجهاز")
    parser.add_argument("--docker", help="مسارٌ مطلق لمحرّك Docker إن لم يكن في موضعه الافتراضي")
    parser.add_argument("--analysis-receipt", type=Path,
                        help="إيصالُ صورة المحلّل (ج٨): تُعلَن analyze_data وتُضبط صورتُها لكل مهمّة (بنكُ ك٥٠)")
    args = parser.parse_args(argv)

    from providers.ollama import OllamaProvider
    provider = OllamaProvider(args.model)
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    try:
        tools = DEFAULT_TOOLS
        if args.analysis_receipt is not None:
            from analysis.tool import ANALYZE_DATA
            tools = (*DEFAULT_TOOLS, ANALYZE_DATA)
        report = run_agentic_suite(suite, provider, ToolRegistry(*tools),
                                   model=args.model, model_version=args.model_version,
                                   max_output=args.max_output,
                                   deadline_s=args.deadline_s,
                                   execution_receipt=args.execution_receipt,
                                   docker_executable=args.docker,
                                   analysis_receipt=args.analysis_receipt)
    except PayloadRejected as exc:
        print(json.dumps({"status": "refused", "code": exc.code,
                          "reason": getattr(exc, "reason", "")}, ensure_ascii=False))
        return 1
    # عتباتُ البنك من ملفّه الجانبي إن كانت (ك٥١): تُحكم على التقرير ولا تُختار بعده
    report = attach_thresholds(report, suite, args.suite)

    raw = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    if args.out:
        if args.out.exists():
            print(json.dumps({"status": "refused", "code": "output_exists"},
                             ensure_ascii=False))
            return 1
        args.out.write_text(raw, encoding="utf-8")

    summary = report["summary"]
    print(f"=== بنك المهامّ الوكيلة: {report['suite_id']} ===")
    print(f"المحاولات {summary['attempted']} · المقيس {summary['measured']} · "
          f"الأخطاء {summary['errors']} · الناجح {summary['passed']}")
    rate = summary["pass_rate_of_measured"]
    print(f"المعدّل على ما قِيس: {'—' if rate is None else f'{rate * 100:.1f}%'}")
    if summary["forbidden_violations"]:
        print(f"**مخالفاتُ مسارٍ ممنوع: {summary['forbidden_violations']}** "
              f"(نجاحٌ مُبطَل، لا إخفاقُ قدرة)")
    for capability, stats in sorted(report["by_capability"].items()):
        errors = f" (أخطاء {stats['errors']})" if stats["errors"] else ""
        print(f"  • {capability:24s} {stats['passed']}/{stats['measured']}{errors}")
    if "thresholds" in report:
        verdict = report["thresholds"]
        print("العتبات: " + ("مستوفاة" if verdict["meets_thresholds"]
                            else "لم تُستوفَ — " + "، ".join(verdict["unmet"])))
    print("الحدودُ المعلنة: " + " · ".join(report["measurement_limits"]))
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
