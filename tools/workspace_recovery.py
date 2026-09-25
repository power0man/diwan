#!/usr/bin/env python3
"""فحص staging ونقل مدخل عالق إلى حجر خاص جديد بعد إغلاق الواجهة؛ بلا حذف."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workspace_tools.recovery import inspect_staging, quarantine_staging, error_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--root", type=Path, required=True)
    quarantine = sub.add_parser("quarantine")
    for key in ("root", "entry", "sha256", "destination"):
        quarantine.add_argument("--" + key, required=True)
    args = parser.parse_args(argv)
    try:
        result = (inspect_staging(args.root) if args.command == "inspect" else
                  quarantine_staging(args.root, args.entry, args.sha256, args.destination))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"status": "error", "error_code": error_code(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
