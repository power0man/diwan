#!/usr/bin/env python3
"""مسار بشري اختياري تاريخي؛ ق٤٩ يعتمد review_automatically.py لمراجعة الجودة."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.canonical import PayloadRejected
from evaluation.capabilities import load_suite
from evaluation.human_review import load_json, summarize_review, write_review_html


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="إنشاء HTML محلي بلا قرارات مسبقة")
    prepare.add_argument("--report", type=Path, required=True)
    prepare.add_argument("--suite", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True,
                         help="مجلد تحت var/reviews في المستودع")
    importer = commands.add_parser("import", help="فحص ملف قرار وعرض ملخص؛ لا تعديل للتقرير")
    importer.add_argument("--report", type=Path, required=True)
    importer.add_argument("--review", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = load_json(args.report)
        if args.command == "prepare":
            path = write_review_html(report, load_suite(args.suite), args.output_dir,
                                     root=ROOT / "var/reviews")
            result = {"review_html": str(path), "manual_review": "pending", "release_ready": False}
        else:
            result = summarize_review(report, load_json(args.review))
    except (PayloadRejected, OSError) as exc:
        print(json.dumps({"error_code": getattr(exc, "code", "filesystem_error"), "release_ready": False}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
