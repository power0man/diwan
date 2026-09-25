"""جولات بوسائط مجمدة داخل بصمة الطلب، فوق حلقة المحادثة المحكومة."""
from __future__ import annotations

from core.canonical import digest
from conversation import ChatSession, ConversationError
from multimodal.codec import (MEDIA_SYSTEM, MEDIA_CONTEXT_CHARS, encode_request,
                              decode_request, validate_media, validate_context)

MAX_TURNS = 32
MAX_SAVED_INPUT_BYTES = 4 * 1024 * 1024


class MediaAssistantError(ValueError):
    def __init__(self, code, reason):
        self.code, self.reason = code, reason
        super().__init__(f"{reason} [{code}]")


def fail(code, reason):
    raise MediaAssistantError(code, reason)


def present(result):
    envelope = decode_request(result["text"])
    preferences = envelope["preferences"]
    return {**{key: value for key, value in result.items() if key != "text"},
        "user_request": envelope["user_request"], "inputs": {
            "media": [{key: value for key, value in media.items() if key != "data_base64"}
                      for media in envelope["media"]],
            "preferences": None if preferences is None else {
                key: preferences[key] for key in ("revision", "sha256")}}}


class MediaAssistant:
    def __init__(self, session, provider, preferences=None):
        config = session.config
        if (config["system_sha256"] != digest(MEDIA_SYSTEM)
                or config["max_context_chars"] != MEDIA_CONTEXT_CHARS
                or config["max_output"] != 400 or config["deadline_s"] != "180.0"):
            fail("media_session_profile", "جلسة الوسائط تتطلب إعداد مرحلتها المثبت")
        self.session, self.provider, self.preferences = session, provider, preferences

    @classmethod
    def open(cls, root, session_id, *, model, model_version, provider, preferences=None):
        return cls(ChatSession(root, session_id, model=model, model_version=model_version,
                    max_output=400, deadline_s=180, max_context_chars=MEDIA_CONTEXT_CHARS,
                    system=MEDIA_SYSTEM), provider, preferences)

    def ask(self, turn_id, user_request, *, media=()):
        if type(media) is not tuple or len(media) > 1:
            fail("media_count", "وسيط واحد كحد أقصى لكل جولة")
        normalized = tuple(validate_media(item) for item in media)
        # Validate the explicit request independently of current preferences before replay.
        encode_request(user_request, normalized, None)
        history = self.session.history()
        for old in history:
            if old["turn_id"] == turn_id:
                envelope = decode_request(old["text"])
                if envelope["user_request"] != user_request or envelope["media"] != list(normalized):
                    raise ConversationError("turn_conflict", "معرف الجولة مرتبط بمدخلات مختلفة")
                return present(old)
        text = encode_request(user_request, normalized,
            None if self.preferences is None else self.preferences.snapshot())
        try:
            result = self.session.turn(turn_id, text, self.provider, max_turns=MAX_TURNS,
                                       max_saved_input_bytes=MAX_SAVED_INPUT_BYTES,
                                       request_validator=lambda request: validate_context(request.messages))
        except ConversationError as exc:
            if exc.code == "session_admission_limit":
                fail("media_session_limit", "بلغ حفظ الوسائط الحد؛ ابدأ جلسة جديدة")
            raise
        return present(result)

    def replay(self, turn_id):
        for turn in self.session.history():
            if turn["turn_id"] == turn_id:
                return present(turn)
        fail("media_turn_missing", "الجولة غير موجودة في جلسة الوسائط")

    def history(self):
        return [present(turn) for turn in self.session.history()]

    def inspect(self, turn_id):
        for turn in self.session.history():
            if turn["turn_id"] == turn_id:
                envelope = decode_request(turn["text"])
                return {"turn": turn_id, "media": envelope["media"],
                        "preferences": envelope["preferences"], "verification": "unverified"}
        fail("media_turn_missing", "الجولة غير موجودة في جلسة الوسائط")
