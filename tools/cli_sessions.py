"""Separate CLI contracts with explicit, non-migrating legacy inspection."""
from conversation import ChatSession, ConversationError
from conversation.session import ID


def open_session(root, legacy_root, session_id, *, purpose, legacy=False, **config):
    if not isinstance(session_id, str) or ID.fullmatch(session_id) is None:
        raise ConversationError("session_id_invalid", "هوية الجلسة غير صالحة")
    old = legacy_root / session_id
    if legacy:
        if not all((old / name).is_file() for name in (
                "manifest.json", "state.json", "calls.jsonl", "session.lock")):
            raise ConversationError("legacy_session_missing", "ملفات الجلسة القديمة غير مكتملة")
        return ChatSession(legacy_root, session_id, **config)
    if not (root / session_id).exists() and old.exists():
        raise ConversationError("legacy_session_requires_explicit_selection",
            "الجلسة موجودة في الجذر القديم؛ اقرأها عبر --legacy-session أو اختر معرفًا جديدًا")
    return ChatSession(root, session_id, purpose=purpose, **config)
