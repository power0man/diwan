#!/usr/bin/env python3
"""ملفات نصية مختارة وتفضيلات صريحة ومسودات لا تُطبق تلقائيًا."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from conversation import ConversationError
from core.canonical import PayloadRejected
from providers.base import ProviderError
from providers.local_chat import LocalChatProvider
from services.assistant_workspace import AssistantWorkspace, WorkspaceAssistantError, _present
from tools.cli_sessions import open_session
from workspace_tools.files import TextWorkspace, WorkspaceError
from workspace_tools.preferences import Preferences, PreferenceError

CHAT_ROOT = ROOT / "var/cli-workspace"
LEGACY_CHAT_ROOT = ROOT / "var/chat"
PREFERENCES_ROOT = ROOT / "var/preferences"
ARTIFACT_ROOT = ROOT / "var/workspace-output"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session")
    parser.add_argument("--legacy-session", action="store_true",
                        help="عرض replay لجولة قديمة صراحة؛ لا نقل أو استئناف توليد")
    parser.add_argument("--model", default=os.environ.get("DIWAN_CHAT_MODEL"))
    parser.add_argument("--model-version", default=os.environ.get("DIWAN_CHAT_DIGEST"))
    parser.add_argument("--read-root", type=Path, help="جذر قراءة اختاره المستخدم صراحة")
    parser.add_argument("--artifact-root", type=Path, default=ARTIFACT_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="جواب عن طلب وملفات مختارة؛ لا كتابة للجواب تلقائيًا")
    ask.add_argument("--turn", required=True)
    source = ask.add_mutually_exclusive_group(required=True)
    source.add_argument("--message")
    source.add_argument("--stdin", action="store_true")
    ask.add_argument("--file", action="append", default=[], help="مسار نسبي تحت جذر القراءة")
    replay = commands.add_parser("replay", help="عرض الجولة من بياناتها المحفوظة دون فتح المصدر")
    replay.add_argument("--turn", required=True)
    proposal = commands.add_parser("propose", help="عرض مسودة ملف من جواب محفوظ")
    proposal.add_argument("--turn", required=True)
    proposal.add_argument("--output", required=True, help="مسار نسبي تحت جذر المخرجات")
    proposal.add_argument("--request", required=True, help="هوية ثابتة لمنع تكرار الاقتراح")
    review = commands.add_parser("review", help="قراءة المسودة وبصمتها قبل التطبيق")
    review.add_argument("--proposal", required=True)
    apply = commands.add_parser("apply", help="تطبيق صريح لمسودة راجعتها؛ لا استبدال لملف موجود")
    apply.add_argument("--proposal", required=True)
    apply.add_argument("--sha256", required=True, help="بصمة المحتوى الذي راجعته")
    prefs = commands.add_parser("preferences", help="عرض أو تعديل تفضيل صريح، ولا تعلم تلقائي")
    operations = prefs.add_subparsers(dest="operation", required=True)
    operations.add_parser("show")
    setter = operations.add_parser("set")
    setter.add_argument("key")
    setter.add_argument("value")
    setter.add_argument("--revision", type=int, required=True)
    deleter = operations.add_parser("delete")
    deleter.add_argument("key")
    deleter.add_argument("--revision", type=int, required=True)
    args = parser.parse_args(argv)
    if args.legacy_session and args.command != "replay":
        parser.error("--legacy-session يسمح بعرض replay فقط؛ للتوليد اختر معرفًا جديدًا")
    if args.command in ("ask", "replay", "propose"):
        if not all((args.session, args.model, args.model_version)):
            parser.error("يلزم معرف جلسة وهوية المزود وبصمته أو متغيرا البيئة المحليان")
    if args.command == "ask" and args.file and args.read_root is None:
        parser.error("اختيار ملفات يتطلب --read-root صريحًا")
    try:
        if args.command == "preferences":
            preferences = Preferences(PREFERENCES_ROOT)
            if args.operation == "show":
                result = preferences.snapshot()
            elif args.operation == "set":
                result = preferences.set(args.key, args.value, expected_revision=args.revision)
            else:
                result = preferences.delete(args.key, expected_revision=args.revision)
        elif args.command == "replay":
            session = open_session(CHAT_ROOT, LEGACY_CHAT_ROOT, args.session,
                                  purpose="cli-workspace", legacy=args.legacy_session,
                                  model=args.model, model_version=args.model_version)
            if args.legacy_session:
                old = next((turn for turn in session.history(recover=False)
                            if turn["turn_id"] == args.turn), None)
                if old is None:
                    raise WorkspaceAssistantError("workspace_turn_missing", "الجولة غير موجودة في الجلسة")
                result = _present(old)
            else:
                result = AssistantWorkspace(session, None, None).replay(args.turn)
        else:
            if args.command in ("ask", "propose"):
                session = open_session(CHAT_ROOT, LEGACY_CHAT_ROOT, args.session,
                                      purpose="cli-workspace", model=args.model,
                                      model_version=args.model_version)
            workspace = TextWorkspace(args.read_root, args.artifact_root)
            if args.command == "review":
                result = workspace.review(args.proposal)
            elif args.command == "apply":
                result = workspace.apply(args.proposal, args.sha256)
            else:
                # Stored replay/proposal do not initialize a provider or preferences store.
                assistant = AssistantWorkspace(session,
                    LocalChatProvider(args.model, args.model_version) if args.command == "ask" else None,
                    workspace, Preferences(PREFERENCES_ROOT) if args.command == "ask" else None)
                if args.command == "ask":
                    request = sys.stdin.read(24001) if args.stdin else args.message
                    result = assistant.ask(args.turn, request, files=tuple(args.file))
                else:
                    result = assistant.propose_answer(args.turn, args.output, args.request)
    except (WorkspaceError, PreferenceError, WorkspaceAssistantError, ConversationError,
            PayloadRejected, ProviderError, OSError) as exc:
        print(json.dumps({"error_code": getattr(exc, "code", "filesystem_error"),
                          "release_ready": False}, ensure_ascii=True))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"error_code": "interrupted", "release_ready": False}))
        return 130
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 1 if result.get("status") in ("error", "truncated") else 0


if __name__ == "__main__":
    raise SystemExit(main())
