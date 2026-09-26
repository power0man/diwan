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
from pathlib import Path
import shlex
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.translation_runner import run_bank  # noqa: E402

REGISTRY = ROOT / "registry" / "agents.json"


def _digest(model: str, base: str = "http://127.0.0.1:11434") -> str | None:
    """بصمةُ النموذج المثبَّت كما يعرضها Ollama، فالوسمُ وحده يتغيّر بسحبٍ جديد."""
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=10) as response:
            models = json.loads(response.read().decode("utf-8"))["models"]
    except (OSError, ValueError, KeyError):
        return None
    wanted = model if ":" in model else model + ":latest"
    return next((m.get("digest") for m in models if m.get("name") == wanted), None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True, help="اسمُ النموذج كما يعرفه Ollama")
    parser.add_argument("--model-version", help="بصمةُ النموذج؛ تُقرأ من Ollama إن لم تُعطَ")
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
    model_version = args.model_version or _digest(args.model) or "unspecified"
    report = run_bank(OllamaProvider(args.model), model=args.model, model_version=model_version,
                      max_steps=args.max_steps, deadline_s=args.deadline_s)
    report.update(agent=args.agent, date=datetime.date.today().isoformat(),
                  engine={"provider": "ollama", "model": args.model, "model_version": model_version,
                          "license": args.license},
                  sampling={"temperature": 0, "seed": SAMPLING_SEED},
                  command=shlex.join(["python3", "tools/evaluate_translation.py", *(argv if argv is not None
                                                                                  else sys.argv[1:])]))
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
