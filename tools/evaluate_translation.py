#!/usr/bin/env python3
"""يشغّل بنكَ الترجمة (غ٤) بمحرّكٍ في Ollama المحلي، ويكتب تقريرَه.

    python3 tools/evaluate_translation.py --model qwen3.5:9b --license Apache-2.0 --agent <معرّفك> \
        --out docs/probe/g4-translation-<التاريخ>.json

لا شبكةَ إلا إلى Ollama المحلي. والعتباتُ في `evaluation/suites/translation_v1.meta.json`، مسجَّلةٌ قبل أيّ تشغيل
(`docs/TRANSLATION-BANK.md`). وكلُّ تقريرٍ يحمل ما تطلبه الخطة لدليل الماك (`docs/PLAN-20260926.md`، claude-mac):
المُشغِّلَ والتاريخَ والمحرّكَ ببصمته ورخصته والبذرةَ والأمرَ الحرفيّ الذي يعيده.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import shlex
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.translation_runner import run_bank  # noqa: E402
from tools.model_digest import (ModelDigestError, pin_model_digest, resolve_model_digest,
                                verify_model_digest)  # noqa: E402

REGISTRY = ROOT / "registry" / "agents.json"


def _digest(model: str) -> str | None:
    """غلافُ توافقٍ لاختبارات المُشغِّل؛ الحلُّ الواحد في ``tools.model_digest``."""
    return resolve_model_digest(model)


def _interpreter() -> str:
    """المفسّرُ الذي قاس، نسبيًّا إلى المستودع إن كان فيه (‎.venv)؛ وإلا اسمُه وحده، فالمسارُ المطلق يحمل اسمَ الحساب
    المحليّ ولا يدخل دليلًا عامًّا (ك٢٧)."""
    executable = Path(sys.executable)
    try:
        return os.path.relpath(executable, ROOT) if executable.resolve().is_relative_to(ROOT.resolve()) else executable.name
    except ValueError:
        return executable.name


def _reproduction(args: argparse.Namespace, model_version: str) -> str:
    """الأمرُ الذي يعيد القياس، مبنيًّا من الوسائط بعد تحليلها لا من نصّها (ملاحظتا Codex على #131):
    البصمةُ المحلولة مثبَّتةٌ فيه، فوسمٌ أُعيد توجيهُه يُردّ بـ`model_version_mismatch` ولا يُقاس بأوزانٍ أخرى؛
    والتقريرُ إلى مسارٍ جديد أيًّا كانت كتابةُ `--out` (‎./ أو ‎--out=)، فلا يردّه `output_exists`."""
    parts = ["--model", args.model, "--model-version", model_version, "--license", args.license, "--agent", args.agent,
             "--max-steps", str(args.max_steps), "--deadline-s", str(args.deadline_s)]
    if args.out is not None:
        parts += ["--out", str(args.out.with_name(args.out.stem + ".rerun" + args.out.suffix))]
    return shlex.join([_interpreter(), "tools/evaluate_translation.py", *parts])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True, help="اسمُ النموذج كما يعرفه Ollama")
    parser.add_argument("--model-version", help="بصمةُ النموذج المتوقَّعة؛ تُقارَن بما يعرضه Ollama")
    parser.add_argument("--license", required=True, help="رخصةُ أوزان النموذج كما في بطاقته")
    parser.add_argument("--agent", required=True, help="معرّفُ من يشغّل القياس، مسجَّلًا في registry/agents.json")
    parser.add_argument("--out", type=Path, help="مسارُ التقرير؛ لا يُستبدل ملفٌّ قائم")
    parser.add_argument("--max-steps", type=int, default=4)
    parser.add_argument("--deadline-s", type=float, default=180.0)
    args = parser.parse_args(argv)
    if args.out is not None and args.out.exists():
        print(json.dumps({"status": "refused", "code": "output_exists"}, ensure_ascii=False))
        return 1
    if args.agent not in json.loads(REGISTRY.read_text(encoding="utf-8"))["agents"]:
        print(json.dumps({"status": "refused", "code": "agent_unregistered"}, ensure_ascii=False))
        return 1
    from providers.ollama import SAMPLING_SEED, OllamaProvider
    # البصمةُ من Ollama قبل أيّ نداءٍ وبعد آخره، ولا تقريرَ بدونها أو إن تغيّرت (على نسق evaluate_memory)
    try:
        model_version = pin_model_digest(args.model, args.model_version, resolver=_digest)
    except ModelDigestError as exc:
        print(json.dumps({"status": "refused", "code": exc.code}, ensure_ascii=False))
        return 1
    report = run_bank(OllamaProvider(args.model), model=args.model, model_version=model_version,
                      max_steps=args.max_steps, deadline_s=args.deadline_s)
    try:
        verify_model_digest(args.model, model_version, resolver=_digest)
    except ModelDigestError as exc:
        print(json.dumps({"status": "refused", "code": exc.code}, ensure_ascii=False))
        return 1
    report.update(agent=args.agent, date=datetime.date.today().isoformat(),
                  engine={"provider": "ollama", "model": args.model, "model_version": model_version,
                          "license": args.license},
                  sampling={"temperature": 0, "seed": SAMPLING_SEED}, python=platform.python_version(),
                  command=_reproduction(args, model_version))
    if args.out is not None:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    summary = report["summary"]
    print(json.dumps({key: summary[key] for key in ("attempted", "measured", "errors", "passed", "pass_rate",
                                                    "check_pass_rate", "meets_thresholds")}, ensure_ascii=False))
    for category, stats in summary["categories"].items():
        print(f"  • {category:10s} {stats['passed']}/{stats['measured']}")
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
