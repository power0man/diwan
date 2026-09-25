#!/usr/bin/env python3
"""محادثة عربية محلية تجريبية؛ الجواب العام غير موثق بمصادر تلقائيًا."""
from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from conversation import ConversationError
from core.canonical import PayloadRejected
from core.contracts import Message, Request
from core.router_sovereign import PIISanitizer, SovereignRouter, SovereignRoutingError
from providers.base import ProviderError
from providers.local_chat import LocalChatProvider
from tools.cli_sessions import open_session

CHAT_ROOT = ROOT / "var/cli-chat"
LEGACY_CHAT_ROOT = ROOT / "var/chat"


def terminal_text(text: str) -> str:
    """عرض النص دون تنفيذ محارف تحكم الطرفية؛ النص الأصلي يبقى في السجل."""
    return "".join(ch if ch in "\n\t" or unicodedata.category(ch)[0] != "C"
                   else f"\\u{ord(ch):04x}" for ch in text)


def _json(value):
    # ASCII escaping also protects terminals from control/bidi sequences in JSON.
    print(json.dumps(value, ensure_ascii=True, allow_nan=False))


def _show(result):
    print(terminal_text(result["content"]))
    print(f"[{result['status']}; {result['turn_id']}; غير موثق]", file=sys.stderr)
    if result["error_code"]:
        print(terminal_text(result["error_code"]), file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, help="معرف جلسة ثابت لاستئنافها")
    parser.add_argument("--legacy-session", action="store_true",
                        help="قراءة history للجلسة القديمة صراحة؛ لا نقل أو استئناف توليد")
    parser.add_argument("--model", default=os.environ.get("DIWAN_CHAT_MODEL"),
                        help="هوية المزود المحلي أو DIWAN_CHAT_MODEL")
    parser.add_argument("--model-version", default=os.environ.get("DIWAN_CHAT_DIGEST"),
                        help="بصمة artifact المحلي أو DIWAN_CHAT_DIGEST")
    parser.add_argument("--max-output", type=int, default=800)
    parser.add_argument("--deadline", type=int, default=120)
    parser.add_argument("--max-context-chars", type=int, default=24000)
    parser.add_argument("--tier", choices=("local_edge", "sovereign_cloud", "frontier_zdr"),
                        default="local_edge", help="مستوى التوجيه السيادي (افتراضي: local_edge)")
    parser.add_argument("--data-policy", choices=("local_only", "regulated", "internal", "public"),
                        default="local_only", help="تصنيف سرية البيانات (افتراضي: local_only)")
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="جولة واحدة؛ كرر معرفها ونصها لإعادة العرض")
    ask.add_argument("--turn", required=True)
    source = ask.add_mutually_exclusive_group(required=True)
    source.add_argument("--message")
    source.add_argument("--stdin", action="store_true", help="قراءة النص من stdin")
    commands.add_parser("history", help="عرض تاريخ الجلسة من القرص بلا نداء")
    commands.add_parser("chat", help="محادثة تفاعلية؛ /exit للخروج و/history للتاريخ")
    args = parser.parse_args(argv)
    if not args.model or not args.model_version:
        parser.error("يلزم تحديد الهوية والبصمة محليًا عبر الخيارات أو متغيري البيئة")
    if args.legacy_session and args.command != "history":
        parser.error("--legacy-session يسمح بعرض history فقط؛ للتوليد اختر معرفًا جديدًا")
    try:
        session = open_session(CHAT_ROOT, LEGACY_CHAT_ROOT, args.session,
                               purpose="cli-chat", legacy=args.legacy_session, model=args.model,
                               model_version=args.model_version,
                               max_output=args.max_output, deadline_s=args.deadline,
                               max_context_chars=args.max_context_chars)
        if args.command == "history":
            _json({"session_id": args.session, "turns": session.history(recover=not args.legacy_session),
                   "quality_review": "pending", "release_ready": False})
            return 0
        provider = LocalChatProvider(args.model, args.model_version)
        router = SovereignRouter()
        if args.command == "ask":
            text = sys.stdin.read(args.max_context_chars + 1) if args.stdin else args.message
            probe_req = Request(
                messages=(Message("user", text),),
                model=args.model,
                model_version=args.model_version,
                max_output=args.max_output,
                deadline_s=args.deadline,
                data_policy=args.data_policy,
                idempotency_key=None,
            )
            router.determine_tier(probe_req, target_tier=args.tier)
            effective_text = text
            token_map = {}
            if args.tier == "frontier_zdr":
                effective_text, token_map = PIISanitizer.sanitize(text)
                if token_map:
                    print(f"[تعقيم سيادي]: تم حجب {len(token_map)} من البيانات الحساسة.", file=sys.stderr)
            result = session.turn(args.turn, effective_text, provider)
            if token_map and result.get("content"):
                result["content"] = PIISanitizer.desanitize(result["content"], token_map)
            result["sovereign_tier"] = args.tier
            result["data_policy"] = args.data_policy
            _json(result)
            return 0 if result["status"] == "complete" else 1
        print("ديوان — تجربة محلية. الأجوبة تحتاج مراجعة؛ /exit للخروج، /history للتاريخ.",
              file=sys.stderr)
        while True:
            try:
                text = input("أنت: ")
            except EOFError:
                return 0
            if text == "/exit":
                return 0
            if text == "/history":
                for item in session.history():
                    print("أنت: " + terminal_text(item["text"]))
                    _show(item)
                continue
            if not text.strip():
                continue
            turn_id = "t-" + uuid.uuid4().hex
            print(f"الجولة: {turn_id}", file=sys.stderr, flush=True)
            try:
                probe_req = Request(
                    messages=(Message("user", text),),
                    model=args.model,
                    model_version=args.model_version,
                    max_output=args.max_output,
                    deadline_s=args.deadline,
                    data_policy=args.data_policy,
                    idempotency_key=None,
                )
                router.determine_tier(probe_req, target_tier=args.tier)
                effective_text = text
                token_map = {}
                if args.tier == "frontier_zdr":
                    effective_text, token_map = PIISanitizer.sanitize(text)
                    if token_map:
                        print(f"[تعقيم سيادي]: تم حجب {len(token_map)} من البيانات الحساسة.", file=sys.stderr)
                res = session.turn(turn_id, effective_text, provider)
                if token_map and res.get("content"):
                    res["content"] = PIISanitizer.desanitize(res["content"], token_map)
                res["sovereign_tier"] = args.tier
                res["data_policy"] = args.data_policy
                _show(res)
            except (ConversationError, PayloadRejected, ProviderError, SovereignRoutingError) as exc:
                print(terminal_text(exc.code), file=sys.stderr)
    except KeyboardInterrupt:
        print("توقفت المحادثة. أعد استعمال معرف الجولة ونصها لاسترجاع حالتها قبل محاولة جديدة.",
              file=sys.stderr)
        return 130
    except (ConversationError, PayloadRejected, ProviderError, SovereignRoutingError, OSError) as exc:
        _json({"error_code": getattr(exc, "code", "filesystem_error"),
               "release_ready": False})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
