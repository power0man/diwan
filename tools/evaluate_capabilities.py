#!/usr/bin/env python3
"""تشخيص development محلي؛ exit 0 اكتمال الجمع، وليس اجتياز الجودة."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.capabilities import CapabilityError, evaluate_suite, load_suite
from providers.ollama import OllamaProvider


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path,
                        default=ROOT / "evaluation/suites/arabic_general_v1.json")
    parser.add_argument("--model", required=True, help="هوية النموذج المحلي وقت التشغيل")
    parser.add_argument("--model-version", default="unspecified",
                        help="معرف أوزان محدد إن توفر؛ الافتراضي لا يثبت نسخة الأوزان")
    parser.add_argument("--run-id", help="هوية اختيارية؛ نفس الإعداد يعاد عرضه افتراضيًا")
    parser.add_argument("--max-output", type=int, default=800)
    parser.add_argument("--deadline-s", type=int, default=240)
    parser.add_argument("--allow-thinking", action="store_true",
                        help="قبول حقل thinking في جواب النماذج الاستدلالية")
    # الافتراض مُطفأ لأن هذه أداةُ قياسٍ للنموذج عاريًا؛ ومسارُ المنتج
    # (conversation.ChatSession) يَحجُر دائمًا بلا علم. وبتشغيله تقاس
    # المنفعةُ التي تشتريها الحوكمة على ag14 وag2_07.
    parser.add_argument("--quarantine-quoted", action="store_true",
                        help="حَجرُ الأوامر داخل المادة المقتبسة قبل النداء (ذراع محكومة)")
    args = parser.parse_args(argv)
    try:
        provider = OllamaProvider(args.model)
        if getattr(args, "allow_thinking", False) or "cloud" in args.model or "oss" in args.model:
            setattr(provider, "allow_thinking", True)
        report = evaluate_suite(load_suite(args.suite), provider,
                                ROOT / "var/capabilities", run_id=args.run_id,
                                max_output=args.max_output, deadline_s=args.deadline_s,
                                model_version=args.model_version,
                                quarantine_quoted_material=args.quarantine_quoted)
    except CapabilityError as exc:
        print(json.dumps({"error_code": exc.code, "release_ready": False}))
        return 2
    except OSError:
        print(json.dumps({"error_code": "filesystem_error", "release_ready": False}))
        return 2
    print(json.dumps({"suite_id": report["suite_id"], "run_id": report["run_id"],
                      **report["summary"]}, ensure_ascii=False))
    return 0 if report["summary"]["collection_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
