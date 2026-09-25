#!/usr/bin/env python3
"""صورة PNG أو صوت PCM WAV مختاران، مع جلسة محلية واسترجاع بلا نداء."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from multimodal.codec import read_selected
from providers.local_media import LocalMediaProvider
from services.media_assistant import MediaAssistant


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "var/media-chat")
    parser.add_argument("--session", required=True)
    parser.add_argument("--model", default=os.environ.get("DIWAN_MEDIA_MODEL"))
    parser.add_argument("--model-version", default=os.environ.get("DIWAN_MEDIA_DIGEST"))
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask")
    ask.add_argument("--turn", required=True)
    ask.add_argument("--message", required=True)
    ask.add_argument("--file", type=Path, help="PNG أو WAV صريح؛ لا قراءة تلقائية لمجلد")
    for name in ("replay", "inspect"):
        commands.add_parser(name).add_argument("--turn", required=True)
    commands.add_parser("history")
    args = parser.parse_args(argv)
    if not args.model or not args.model_version:
        parser.error("يلزم ضبط هوية المزود المثبت وبصمته محليًا")
    try:
        provider = LocalMediaProvider(args.model, args.model_version) if args.command == "ask" else None
        assistant = MediaAssistant.open(args.root, args.session, model=args.model,
            model_version=args.model_version, provider=provider)
        if args.command == "ask":
            result = assistant.ask(args.turn, args.message,
                media=() if args.file is None else (read_selected(args.file),))
        elif args.command == "history":
            result = {"turns": assistant.history()}
        else:
            result = getattr(assistant, args.command)(args.turn)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"error_code": getattr(exc, "code", "media_operation_failed"),
                          "release_ready": False}))
        return 2
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 1 if result.get("status") in ("error", "truncated") else 0


if __name__ == "__main__":
    raise SystemExit(main())
