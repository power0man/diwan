#!/usr/bin/env python3
"""تشخيص development محلي؛ exit 0 اكتمال الجمع، وليس اجتياز الجودة."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.capabilities import CapabilityError, evaluate_suite, load_suite
from providers.ollama import OllamaProvider
from tools.model_digest import ModelDigestError, pin_model_digest, quarantine_runs_since, verify_model_digest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path,
                        default=ROOT / "evaluation/suites/arabic_general_v1.json")
    parser.add_argument("--model", required=True, help="هوية النموذج المحلي وقت التشغيل")
    parser.add_argument("--model-version",
                        help="بصمةُ النموذج المتوقَّعة؛ تُقارَن بما يعرضه Ollama، وبدونها تُحلّ منه")
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
    # البصمةُ تُحلّ قبل القياس فتدخل هويّةَ التشغيلة، وتُعاد بعده (#285). وتشغيلةٌ تغيّرت أوزانُها أثناءها تُرفض برمزها
    # وتُنقل إلى الحجر فلا يعيد استدعاءٌ تالٍ عرضَها ببصمةٍ عادت كما كانت (ملاحظة Codex على #290).
    run_root = ROOT / "var/capabilities"
    started = time.time() - 1
    try:
        model_version = pin_model_digest(args.model, args.model_version)
    except ModelDigestError as exc:
        print(json.dumps({"error_code": exc.code, "release_ready": False}))
        return 2
    failure: BaseException | None = None
    try:
        provider = OllamaProvider(args.model)
        if getattr(args, "allow_thinking", False) or "cloud" in args.model or "oss" in args.model:
            setattr(provider, "allow_thinking", True)
        report = evaluate_suite(load_suite(args.suite), provider,
                                run_root, run_id=args.run_id,
                                max_output=args.max_output, deadline_s=args.deadline_s,
                                model_version=model_version,
                                quarantine_quoted_material=args.quarantine_quoted)
    except BaseException as exc:          # يُعاد التحقّقُ والحجرُ على كلِّ خروجٍ بعد بدء القياس (ملاحظة Codex على #290)
        failure = exc
    try:
        verify_model_digest(args.model, model_version)
    except ModelDigestError as exc:
        quarantined = quarantine_runs_since(run_root, started)
        print(json.dumps({"error_code": exc.code, "release_ready": False, "runs_quarantined": len(quarantined)}))
        return 2
    if isinstance(failure, CapabilityError):
        print(json.dumps({"error_code": failure.code, "release_ready": False}))
        return 2
    if isinstance(failure, OSError):
        print(json.dumps({"error_code": "filesystem_error", "release_ready": False}))
        return 2
    if failure is not None:
        raise failure
    print(json.dumps({"suite_id": report["suite_id"], "run_id": report["run_id"],
                      **report["summary"]}, ensure_ascii=False))
    return 0 if report["summary"]["collection_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
