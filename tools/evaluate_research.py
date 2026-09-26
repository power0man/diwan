#!/usr/bin/env python3
"""يشغّل بنكَ البحث المعمّق (ك٥٣) بمحرّكٍ في Ollama المحلي، ويكتب تقريرَه.

    python3 tools/evaluate_research.py --model qwen3.5:9b --out docs/probe/k53-research-<التاريخ>.json

البحثُ على المحرّك الثابت لمتن البنك، فلا شبكةَ إلا إلى Ollama المحلي. والعتبات في
`evaluation/suites/research_v1.meta.json`، مسجَّلةٌ قبل أيّ تشغيل (`docs/RESEARCH-BANK.md`).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.research_runner import run_bank  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True, help="اسمُ النموذج كما يعرفه Ollama")
    parser.add_argument("--model-version", default="unspecified")
    parser.add_argument("--out", type=Path, help="مسارُ التقرير؛ لا يُستبدل ملفٌّ قائم")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--deadline-s", type=float, default=180.0)
    args = parser.parse_args(argv)
    if args.out is not None and args.out.exists():
        print(json.dumps({"status": "refused", "code": "output_exists"}, ensure_ascii=False))
        return 1
    from providers.ollama import OllamaProvider
    report = run_bank(OllamaProvider(args.model), model=args.model, model_version=args.model_version,
                      max_steps=args.max_steps, deadline_s=args.deadline_s)
    if args.out is not None:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    summary = report["summary"]
    print(json.dumps({key: summary[key] for key in ("attempted", "measured", "errors", "passed", "pass_rate",
                                                    "citation_valid_rate", "fabricated_sources", "meets")},
                     ensure_ascii=False))
    for category, stats in summary["by_category"].items():
        print(f"  • {category:13s} {stats['passed']}/{stats['measured']}")
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
