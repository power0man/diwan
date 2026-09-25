#!/usr/bin/env python3
"""نسخة خاصة لمساحة ديوان المحلية واستعادة إلى جذر جديد، دون مزود أو نشر."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workspace_tools.backup import export_workspace, inspect_archive, restore_workspace


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("backup")
    export.add_argument("--root", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--archive", type=Path, required=True)
    inspect.add_argument("--sha256", required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    restore.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            result = export_workspace(args.root, args.output)
        elif args.command == "inspect":
            result = inspect_archive(args.archive, args.sha256)
        else:
            result = restore_workspace(args.archive, args.destination, args.sha256)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"status": "error", "error_code": getattr(exc, "code", "backup_operation_failed")}, ensure_ascii=True))
        return 2
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
