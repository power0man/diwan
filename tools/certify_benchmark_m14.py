#!/usr/bin/env python3
"""Validate automatic reviews of an existing benchmark; never impersonate a human."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evaluation.benchmark_review import BENCHMARK_RUBRIC, review_benchmark
from evaluation.multi_system_review import AutomaticReviewError, parse_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--export-rubric", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.export_rubric:
            with args.export_rubric.open("x", encoding="utf-8") as stream:
                json.dump(BENCHMARK_RUBRIC, stream, ensure_ascii=False, indent=2)
            return 0
        if not all((args.results, args.review, args.output)):
            parser.error("--results, --review and a new --output are required")
        receipt = review_benchmark(parse_json(args.results.read_bytes()), parse_json(args.review.read_bytes()))
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
        print(json.dumps({"status": receipt["status"], "human_review": False,
                          "product_readiness": "not_assessed"}))
        return 0 if receipt["status"] == "automated_review_accepted" else 2
    except (AutomaticReviewError, OSError) as exc:
        print(json.dumps({"status": "inconclusive", "error": getattr(exc, "code", "io_error")}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
