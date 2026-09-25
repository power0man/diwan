"""حوار نصي دائم، بقفل وجولات لا تعيد النداء بعد الانقطاع المجهول.

تتحقق البصمات من فساد الملفات، لا من مالك يعيد كتابة الحالة والسجل
معًا أو يستعيد نسخة قديمة منهما. المحليّة عقد مزود؛ توثيق النقل على
عاتق محول المزود. السياق هو الأزواج المكتملة فقط ولا يصير ذاكرة دائمة.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid

from core.budget import Budget
from core.canonical import canonical_bytes, digest
from core.contracts import Message, Request, Response, Usage
from core.ledger import GENESIS, Ledger, LedgerCorrupt
from core.quoted import quarantine_quoted
from core.run import _check_response, execute
from core.validate import validated

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
SYSTEM = (
    "أنت مساعد ديوان العربي العام. أجب بالعربية ما لم يطلب المستخدم لغة أخرى. "
    "احترم تصحيحات المستخدم وقيوده الأحدث. وضّح ما لا تعرفه ولا تختلق مصادر. "
    "هذه محادثة نصية فقط: لا تتوفر لك أدوات أو ملفات أو ذاكرة خارج هذه الجلسة. "
    "لا تدّع تنفيذ فعل خارجي أو حفظ تفضيل دائم."
)


class ConversationError(ValueError):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{reason} [{code}]")
        self.code, self.reason = code, reason


def _fail(code, reason):
    raise ConversationError(code, reason)


def _id(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def _text(value):
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _path(path, *, directory=False):
    if any(p.is_symlink() for p in (path, *path.parents)):
        _fail("unsafe_path", "روابط رمزية في مسار المحادثة")
    if path.exists():
        info = path.stat()
        if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
                or (not directory and info.st_nlink != 1)):
            _fail("unsafe_path", "مسار غير عادي أو متعدد الروابط")
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            _fail("unsafe_permissions", "المحادثة تتطلب أذونات خاصة بمالكها")


def _directory(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        _fail("unsafe_path", "روابط رمزية في مسار المحادثة")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _path(path, directory=True)


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail("state_corrupt", "مفتاح JSON مكرر")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        canonical_bytes(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ConversationError):
            raise
        _fail("state_corrupt", "بيانات المحادثة غير صالحة")


def _read(path):
    _path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            return _decode(stream.read())
    except (OSError, UnicodeError):
        _fail("state_corrupt", "تعذرت قراءة حالة المحادثة")


def _write(path, value):
    _path(path)
    temp = path.parent / ("write-" + uuid.uuid4().hex + ".tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp.exists():
            temp.unlink()


class ChatSession:
    def __init__(self, root: Path, session_id: str, *, model: str,
                 model_version: str, max_output: int = 800,
                 deadline_s: float = 120, max_context_chars: int = 24000,
                 system: str = SYSTEM, purpose: str | None = None):
        if not _id(session_id):
            _fail("session_id_invalid", "هوية الجلسة غير صالحة")
        if not _text(model) or not _text(model_version):
            _fail("model_invalid", "هوية النموذج وإصداره مطلوبتان")
        if not _text(system) or len(system) > 8000:
            _fail("system_invalid", "تعليمات نظام UTF-8 غير فارغة ومحدودة مطلوبة")
        if purpose is not None and purpose not in ("cli-chat", "cli-workspace"):
            _fail("purpose_invalid", "غرض جلسة غير معروف")
        if (type(max_output) is not int or not 1 <= max_output <= 1_000_000
                or type(max_context_chars) is not int or not 1 <= max_context_chars <= 1_000_000):
            _fail("limits_invalid", "حدود الإخراج والسياق أعداد صحيحة موجبة ومحدودة")
        if (type(deadline_s) not in (int, float) or not math.isfinite(deadline_s)
                or not 0 < deadline_s <= 3600):
            _fail("deadline_invalid", "مهلة موجبة منتهية لا تتجاوز ساعة")
        self.root = Path(root).absolute()
        self.session_id = session_id
        self.directory = self.root / session_id
        self.deadline_s = deadline_s
        self.system = system
        self.config = {
            "schema_version": 1, "session_id": session_id, "model": model,
            "model_version": model_version, "max_output": max_output,
            "deadline_s": str(float(deadline_s)), "max_context_chars": max_context_chars,
            "data_policy": "local_only", "system_sha256": digest(system),
        }
        # Omission preserves every legacy manifest and request without migration.
        if purpose is not None:
            self.config["purpose"] = purpose
        _directory(self.root)
        _directory(self.directory)
        with self._lock():
            manifest = self.directory / "manifest.json"
            if manifest.exists():
                self._load()
            else:
                if any(p.name != "session.lock" for p in self.directory.iterdir()):
                    _fail("manifest_missing", "ملفات سابقة دون بيان؛ لا إعادة تهيئة")
                _write(manifest, self.config)
                fd = os.open(self.directory / "calls.jsonl",
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(fd)
                self._save({"config_sha256": digest(self.config), "head": GENESIS,
                            "count": 0, "turns": []})

    @contextmanager
    def _lock(self):
        _path(self.root, directory=True)
        _path(self.directory, directory=True)
        path = self.directory / "session.lock"
        _path(path)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+") as stream:
            if os.fstat(stream.fileno()).st_nlink != 1:
                _fail("unsafe_path", "قفل متعدد الروابط")
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                _fail("session_busy", "الجلسة قيد التنفيذ في عملية أخرى")
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _save(self, state):
        _write(self.directory / "state.json", {"state": state, "sha256": digest(state)})

    def _messages(self, turns, text):
        """ما يُرسل إلى النموذج محجورُ الأوامرِ المقتبسة؛ والسجلّ يحفظ الأصل.

        `core.quoted.quarantine_quoted` يَحجُر الأوامر داخل المناطق المقتبسة
        وحدها، فأمرُ صاحبِ الطلب باقٍ. والدالّة محضةٌ فلا يختلف إعادةُ العرض.
        والنصُّ الأصليّ يبقى في `turn["text"]` — درءٌ بالسجل لا بالإزاحة.
        """
        messages = [Message("system", self.system)]
        for turn in turns:
            result = turn["result"]
            if result is not None and result["status"] == "complete":
                messages.extend((Message("user", quarantine_quoted(turn["text"]).text),
                                 Message("assistant", result["content"])))
        messages.append(Message("user", quarantine_quoted(text).text))
        return tuple(messages)

    def _request(self, turns, turn_id, text):
        messages = self._messages(turns, text)
        req = Request(messages, self.config["model"], self.config["model_version"],
                      self.config["max_output"], self.deadline_s, "local_only",
                      self._turn_key(turn_id))
        validated(req)
        return req, digest([{"role": m.role, "content": m.content} for m in messages])

    def _turn_key(self, turn_id):
        return f"{self.config.get('purpose', 'chat')}:{self.session_id}:{turn_id}"

    def _ledger(self):
        path = self.directory / "calls.jsonl"
        _path(path)
        if not path.exists():
            _fail("ledger_corrupt", "سجل النداءات غائب")
        try:
            # Parsing independently rejects duplicates that ordinary json.loads accepts.
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    _fail("ledger_corrupt", "سطر فارغ في السجل")
                _decode(line)
            ledger = Ledger(path, create=False)
            ledger.verify_chain()
            return ledger, ledger.entries()
        except (LedgerCorrupt, ValueError, KeyError, TypeError, OSError, UnicodeError):
            _fail("ledger_corrupt", "سلسلة سجل المحادثة غير صالحة")

    def _result(self, turn, entry=None):
        result = {
            "session_id": self.session_id, "turn_id": turn["turn_id"],
            "text": turn["text"], "kind": "assistant_message", "verification": "unverified",
            "request_sha256": turn["request_sha256"], "context_sha256": turn["context_sha256"],
            "content": "", "status": "error", "error_code": "outcome_uncertain",
            "stop_reason": "error", "usage": {"input_tokens": 0, "output_tokens": 0},
            "cost_micros": 0, "ledger_sha256": None,
        }
        if entry is None:
            return result
        rec = entry["record"]
        expected = {"request_digest": turn["request_sha256"], "model": self.config["model"],
                    "model_version": self.config["model_version"], "data_policy": "local_only",
                    "idempotency_key": self._turn_key(turn["turn_id"])}
        if not isinstance(rec, dict) or any(rec.get(k) != v for k, v in expected.items()):
            _fail("ledger_corrupt", "قيد لا يطابق الجولة المعلقة")
        if not isinstance(rec.get("kind"), str) or rec["kind"] not in {"ok", "error", "refused"}:
            _fail("ledger_corrupt", "نوع قيد غير صالح للمحادثة")
        result["ledger_sha256"] = entry["digest"]
        if rec["kind"] != "ok":
            if not _text(rec.get("error_code")):
                _fail("ledger_corrupt", "قيد خطأ بلا رمز")
            result["error_code"] = rec["error_code"]
            return result
        try:
            raw = rec["response"]
            response = Response(content=raw["content"], usage=Usage(**raw["usage"]),
                                stop_reason=raw["stop_reason"], cost_micros=raw["cost_micros"],
                                provider=raw.get("provider", ""), model_version=raw.get("model_version", ""))
            _check_response(response)
            canonical_bytes(raw)
        except Exception:
            _fail("ledger_corrupt", "جواب مسجل خارج العقد")
        status = ("complete" if response.stop_reason == "complete" else
                  "truncated" if response.stop_reason == "max_output" else "error")
        result.update(content=response.content, status=status, stop_reason=response.stop_reason,
                      error_code=None if status == "complete" else "output_truncated" if status == "truncated"
                      else "provider_" + response.stop_reason,
                      usage=raw["usage"], cost_micros=response.cost_micros)
        return result

    def _load(self):
        if digest(_read(self.directory / "manifest.json")) != digest(self.config):
            _fail("config_conflict", "إعداد الجلسة تغيّر؛ استخدم هوية جلسة جديدة")
        envelope = _read(self.directory / "state.json")
        if not isinstance(envelope, dict) or set(envelope) != {"state", "sha256"}:
            _fail("state_corrupt", "غلاف حالة غير صالح")
        state = envelope["state"]
        if (not isinstance(state, dict) or set(state) != {"config_sha256", "head", "count", "turns"}
                or digest(state) != envelope["sha256"] or state["config_sha256"] != digest(self.config)
                or type(state["count"]) is not int or state["count"] < 0
                or not isinstance(state["turns"], list) or len(state["turns"]) > 1000):
            _fail("state_corrupt", "حالة غير صالحة أو لا تطابق البصمة")
        ledger, entries = self._ledger()
        checkpoint = state["count"]
        if (checkpoint > len(entries) or state["head"] !=
                (entries[checkpoint - 1]["digest"] if checkpoint else GENESIS)):
            _fail("ledger_corrupt", "السجل لا يطابق آخر نقطة حفظ")
        ids, index = set(), 0
        turns = state["turns"]
        for i, turn in enumerate(turns):
            if (not isinstance(turn, dict) or set(turn) !=
                    {"turn_id", "text", "request_sha256", "context_sha256", "result"}
                    or not _id(turn["turn_id"]) or turn["turn_id"] in ids or not _text(turn["text"])):
                _fail("state_corrupt", "هوية جولة أو نص أو ترتيب غير صالح")
            ids.add(turn["turn_id"])
            req, context = self._request(turns[:i], turn["turn_id"], turn["text"])
            if (turn["request_sha256"] != digest(req.fingerprint_payload())
                    or turn["context_sha256"] != context
                    or sum(len(m.content) for m in req.messages) > self.config["max_context_chars"]):
                _fail("state_corrupt", "بصمة الجولة لا تطابق سياقها")
            result = turn["result"]
            if result is None:
                if i != len(turns) - 1:
                    _fail("state_corrupt", "جولة معلقة ليست الأخيرة")
            else:
                if not isinstance(result, dict):
                    _fail("state_corrupt", "نتيجة الجولة غير صالحة")
                entry = None
                if result.get("ledger_sha256") is not None:
                    if index >= checkpoint:
                        _fail("state_corrupt", "جولة بلا قيد معتمد")
                    entry = entries[index]
                    index += 1
                if digest(result) != digest(self._result(turn, entry)):
                    _fail("state_corrupt", "نتيجة الجولة لا تطابق سجلها")
        if index != checkpoint:
            _fail("state_corrupt", "قيود معتمدة بلا جولات")
        pending = turns and turns[-1]["result"] is None
        if len(entries) != checkpoint and (not pending or len(entries) != checkpoint + 1):
            _fail("ledger_corrupt", "إلحاق خارج الجولة المعلقة")
        if pending and len(entries) == checkpoint + 1:
            self._result(turns[-1], entries[-1])
        return state, ledger, entries

    def _recover(self, state, entries):
        if state["turns"] and state["turns"][-1]["result"] is None:
            turn = state["turns"][-1]
            entry = entries[-1] if len(entries) > state["count"] else None
            turn["result"] = self._result(turn, entry)
            state.update(count=len(entries), head=entries[-1]["digest"] if entries else GENESIS)
            self._save(state)

    @staticmethod
    def _public(turn, *, replayed):
        return {**copy.deepcopy(turn["result"]), "replayed": replayed}

    def history(self, *, recover=True):
        with self._lock():
            state, _, entries = self._load()
            if recover:
                self._recover(state, entries)
            elif state["turns"] and state["turns"][-1]["result"] is None:
                # Read-only legacy inspection reports the provable result without
                # changing its stored checkpoint or claiming an uncertain call ran.
                entry = entries[-1] if len(entries) > state["count"] else None
                turn = state["turns"][-1]
                turn["result"] = self._result(turn, entry)
            return [self._public(t, replayed=True) for t in state["turns"]]

    def turn(self, turn_id: str, text: str, provider, *, max_turns=None,
             max_saved_input_bytes=None, request_validator=None):
        if not _id(turn_id):
            _fail("turn_id_invalid", "هوية الجولة غير صالحة")
        if not _text(text):
            _fail("text_invalid", "نص UTF-8 غير فارغ مطلوب")
        for limit in (max_turns, max_saved_input_bytes):
            if limit is not None and (type(limit) is not int or limit <= 0):
                _fail("admission_limit_invalid", "حد القبول يجب أن يكون عددًا صحيحًا موجبًا")
        if request_validator is not None and not callable(request_validator):
            _fail("admission_validator_invalid", "حارس الطلب يجب أن يكون قابلًا للاستدعاء")
        with self._lock():
            state, ledger, entries = self._load()
            self._recover(state, entries)
            previous = next((t for t in state["turns"] if t["turn_id"] == turn_id), None)
            if previous is not None:
                if previous["text"] != text:
                    _fail("turn_conflict", "هوية الجولة نفسها لنص مختلف")
                return self._public(previous, replayed=True)
            # Replay is always available; admission uses fresh state under the append lock.
            if ((max_turns is not None and len(state["turns"]) >= max_turns)
                    or (max_saved_input_bytes is not None and
                        sum(len(t["text"].encode("utf-8")) for t in state["turns"])
                        + len(text.encode("utf-8")) > max_saved_input_bytes)):
                _fail("session_admission_limit", "بلغت الجلسة حد الحفظ؛ ابدأ جلسة جديدة")
            if len(state["turns"]) >= 1000:
                _fail("session_limit", "بلغت الجلسة حد ألف جولة؛ ابدأ جلسة جديدة")
            req, context = self._request(state["turns"], turn_id, text)
            if sum(len(m.content) for m in req.messages) > self.config["max_context_chars"]:
                _fail("context_limit", "تجاوز السياق الحد؛ ابدأ جلسة جديدة دون حذف صامت")
            if request_validator is not None:
                # The guard sees the exact fresh context under the append lock,
                # before the provider sees it and before recording a pending turn.
                request_validator(req)
            if getattr(provider, "is_local", None) is not True:
                _fail("policy_requires_local", "المحادثة تتطلب مزودًا محليًا")
            turn = {"turn_id": turn_id, "text": text, "request_sha256": digest(req.fingerprint_payload()),
                    "context_sha256": context, "result": None}
            state["turns"].append(turn)
            self._save(state)  # A crash after here must never cause another provider call.
            interrupted = None
            try:
                execute(req, provider, Budget(0, 0), ledger)
            except (KeyboardInterrupt, SystemExit) as exc:
                interrupted = exc
            except Exception:
                pass  # Recovery uses the durable core result, never an exception's private text.
            state, _, entries = self._load()
            self._recover(state, entries)
            if interrupted is not None:
                raise interrupted
            return self._public(state["turns"][-1], replayed=False)
