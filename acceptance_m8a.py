#!/usr/bin/env python3
"""م٨-أ: جمع تشخيص علني محلي وإثبات إعادة العرض دون نداء جديد.

هذا قبول لمسار القياس، لا حكم بصحة الأجوبة أو جاهزية المساعد.
هوية النموذج المثبت تمرر صراحةً وقت التشغيل ولا تحفظ في المصدر.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from evaluation.capabilities import evaluate_suite, load_suite
from providers.ollama import OllamaProvider

ROOT = Path(__file__).resolve().parent


class CountedLocalProvider(OllamaProvider):
    calls = 0

    def _post(self, payload, timeout):
        self.calls += 1
        return super()._post(payload, timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-version", default="unspecified")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    suite = load_suite(ROOT / "evaluation/suites/arabic_general_v1.json")
    provider = CountedLocalProvider(args.model)
    report = evaluate_suite(suite, provider, ROOT / "var/capabilities",
                            run_id=args.run_id, model_version=args.model_version)
    if not report["summary"]["collection_complete"]:
        print(json.dumps({"checks": {"collection_complete": False},
                          "initial_network_calls": provider.calls,
                          "replay_not_attempted": True,
                          "run_id": report["run_id"], "summary": report["summary"]},
                         ensure_ascii=False))
        return 1
    ledger = ROOT / "var/capabilities" / report["run_id"] / "calls.jsonl"
    before = hashlib.sha256(ledger.read_bytes()).hexdigest()
    live_calls = provider.calls
    replay = evaluate_suite(suite, provider, ROOT / "var/capabilities",
                            run_id=report["run_id"], model_version=args.model_version)
    checks = {
        "collection_complete": report["summary"]["collection_complete"],
        "all_cases_replayed": all(r["replayed"] for r in replay["results"]),
        "zero_new_calls": provider.calls == live_calls,
        "ledger_unchanged": hashlib.sha256(ledger.read_bytes()).hexdigest() == before,
        "answers_unchanged": [r["answer"] for r in report["results"]] ==
                             [r["answer"] for r in replay["results"]],
        "no_release_claim": report["release_ready"] is False and
                            replay["release_ready"] is False,
    }
    print(json.dumps({"checks": checks, "initial_network_calls": live_calls,
                      "replay_network_calls": provider.calls - live_calls,
                      "run_id": report["run_id"], "summary": report["summary"]},
                     ensure_ascii=False))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
