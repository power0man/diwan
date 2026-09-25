"""Version 2 agent sessions: private control state, durable calls and explicit resume.

This is separate from ChatSession v1. Checksums detect corruption, not an owner
rewriting every control file together. Only completed, closed transcripts enter a
later turn. An intent without a durable outcome is never retried automatically.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
import threading
from pathlib import Path
import uuid

from agent.actions import ActionRefused, ActionStore
from agent.journal import Journal, JournalRefused, _identity, _open_directory, _read, _regular
from agent.loop import SYSTEM, _result_message, run_agent
from agent.registry import ToolContext, ToolRegistry
from conversation.agent_stop import StopSignals
from conversation.session import ConversationError, _decode, _fail, _id, _text
from core import filelock
from core.budget import Budget
from core.canonical import canonical_bytes, digest
from core.contracts import Message, Request, ToolCall, ToolSpec, _message_payload
from core.ledger import GENESIS, Ledger, LedgerCorrupt
from core.run import RouteRefused
from core.validate import validated
from services.agent_workspace import model_facing_input

MAX_STATE_BYTES = 32 * 1024 * 1024
MAX_LEDGER_BYTES = 32 * 1024 * 1024
TERMINAL = frozenset({"complete", "truncated", "timed_out", "failed", "refused", "step_limit", "cancelled"})
_TURN_KEYS = frozenset({"turn_id", "text", "initial_messages", "input_digest", "calls", "result", "transcript"})


def _copy(value):
    return _decode(canonical_bytes(value))


def _messages(payload):
    return tuple(Message(item["role"], item["content"], item.get("tool_call_id"),
                         tuple(ToolCall(**call) for call in item.get("tool_calls", ())))
                 for item in payload)


def _user_message(text):
    """رسالةُ المستخدم كما يراها النموذج: محجورةُ الأوامرِ المقتبسة (ج٤).

    الطريقُ النصّي يحجر في `Session._messages` وحدها فيتّفق الإرسالُ والتحقّق.
    وهنا الموضعُ الواحدُ نفسُه: التشغيلُ، وفحصُ الحدّ، وبادئةُ كلّ نداءٍ مسجَّل،
    وإعادةُ بناء التاريخ — كلُّها تمرّ من هنا. و`turn["text"]` يحفظ الأصلَ للسجلّ
    والواجهة، و`input_digest` يُحسب عليه لا على المحجور.
    """
    return Message("user", model_facing_input(text))


class _SessionLedger(Ledger):
    """Ledger contract with descriptor-based IO under the owning session lock."""
    def __init__(self, session):
        self.session = session
        self.path = session.root / "calls.jsonl"

    def entries(self):
        raw = self.session._raw("calls.jsonl", MAX_LEDGER_BYTES)
        if raw is None:
            _fail("state_missing", "سجل نداءات الجلسة مفقود")
        return [_decode(line) for line in raw.splitlines() if line.strip()]

    def append(self, record):
        self.session._check()
        entries = self.entries()
        entry = {"prev": entries[-1]["digest"] if entries else GENESIS,
                 "seq": len(entries), "record": record}
        entry_digest = digest(entry)
        raw = canonical_bytes({**entry, "digest": entry_digest}) + b"\n"
        fd = os.open("calls.jsonl", os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW,
                     dir_fd=self.session._fd)
        try:
            info = os.fstat(fd)
            _regular(info)
            if info.st_mode & 0o077 or info.st_size + len(raw) > MAX_LEDGER_BYTES:
                _fail("ledger_limit", "سجل النداءات غير خاص أو تجاوز الحد")
            with os.fdopen(fd, "ab", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        return entry_digest


class _ProviderGuard:
    def __init__(self, session, state, turn, provider):
        self.session, self.state, self.turn, self.provider = session, state, turn, provider
        self.name = getattr(provider, "name", "local")
        self.is_local = getattr(provider, "is_local", None) is True

    def estimate_micros(self, request):
        # core.execute replays recorded calls before reaching this method. A fresh
        # invocation may begin only once, including the provider's estimate hook.
        payload = request.fingerprint_payload()
        if len(canonical_bytes(payload).decode("utf-8")) > self.session.config["max_context_chars"]:
            raise RouteRefused("context_limit", "سياق الجولة يتجاوز الحد؛ لم يُنادَ المزوّد")
        if any(item["idempotency_key"] == request.idempotency_key for item in self.turn["calls"]):
            raise RouteRefused("provider_retry_forbidden", "نداء بدأ سابقًا؛ لا إعادة تلقائية")
        if len(self.turn["calls"]) >= self.session.config["max_steps"]:
            raise RouteRefused("step_limit", "سقف نداءات الجولة")
        self.turn["calls"].append({"idempotency_key": request.idempotency_key,
                                   "request_digest": digest(payload), "request": payload})
        self.session._save(self.state)  # fsync before even the estimate sees input.
        return self.provider.estimate_micros(request)

    def complete(self, request):
        return self.provider.complete(request)


class AgentSession:
    """Trusted bootstrap supplies all identities, registry and workspace settings."""
    def __init__(self, control_root: Path, session_id: str, *, workspace_root: Path,
                 project_id: str, registry: ToolRegistry, model: str, model_version: str,
                 max_steps: int = 8, max_output: int = 1024, deadline_s: float = 120,
                 max_context_chars: int = 24000, max_turns: int = 128,
                 system: str = SYSTEM):
        if not _id(session_id) or not _id(project_id):
            _fail("session_identity_invalid", "هوية مشروع وجلسة صريحتان مطلوبتان")
        if not isinstance(registry, ToolRegistry) or not _text(model) or not _text(model_version):
            _fail("configuration_invalid", "سجل أدوات وهوية نموذج ثابتة مطلوبان")
        if not _text(system) or len(system) > 8000:
            _fail("configuration_invalid", "تعليمات نظام محدودة مطلوبة")
        for value, maximum in ((max_steps, 64), (max_output, 4096),
                               (max_context_chars, 200000), (max_turns, 128)):
            if type(value) is not int or not 1 <= value <= maximum:
                _fail("configuration_invalid", "حدود الجلسة غير صالحة")
        if (type(deadline_s) not in (int, float) or not 0 < deadline_s <= 3600
                or not math.isfinite(deadline_s)):
            _fail("configuration_invalid", "مهلة محدودة موجبة مطلوبة")
        self.root = Path(control_root).absolute() / session_id
        self.workspace = Path(workspace_root).absolute()
        if ".." in self.root.parts or ".." in self.workspace.parts:
            _fail("unsafe_path", "مسارات دون عبور مطلوبة")
        if self.root == self.workspace or self.workspace in self.root.parents:
            _fail("control_root_in_workspace", "دليل التحكم يجب أن يكون خارج مساحة الفعل")
        self.session_id, self.project_id, self.registry = session_id, project_id, registry
        self._fd = None
        self._thread_lock = threading.Lock()
        try:
            workspace_fd = _open_directory(self.workspace)
            try:
                self._workspace_identity = _identity(os.fstat(workspace_fd))
            finally:
                os.close(workspace_fd)
            root_fd = _open_directory(self.root, create=True)
            try:
                self._root_identity = _identity(os.fstat(root_fd))
                self._private_dir(root_fd)
            finally:
                os.close(root_fd)
        except (OSError, JournalRefused):
            _fail("unsafe_path", "مسار جلسة أو مساحة عمل غير آمن")
        self.config = {"schema_version": 2, "session_id": session_id, "project_id": project_id,
                       "workspace": {"path": str(self.workspace),
                                     "device": str(self._workspace_identity[0]),
                                     "inode": str(self._workspace_identity[1])},
                       "model": model, "model_version": model_version,
                       "max_steps": max_steps, "max_output": max_output,
                       "deadline_s": str(float(deadline_s)), "max_context_chars": max_context_chars,
                       "max_turns": max_turns, "system": system,
                       "tools": _copy([spec.declared() for spec in registry.specs()])}
        self._stop_signals = StopSignals(self.root, self.workspace, self._root_identity,
                                         self._workspace_identity, self.config)
        self.ledger = _SessionLedger(self)
        self.journal = Journal(self.workspace)
        with self._lock():
            fresh = self._raw("manifest.json") is None
            if fresh:
                if set(os.listdir(self._fd)) != {"session.lock"}:
                    _fail("state_missing", "لا يعاد إنشاء بيان جلسة لها ملفات سابقة")
                self._write("manifest.json", self.config)
                fd = os.open("calls.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self._fd)
                os.fsync(fd)
                os.close(fd)
                self._save({"schema_version": 2, "config_digest": digest(self.config),
                            "turns": []})
            if not fresh:
                if _decode(self._raw("manifest.json")) != self.config:
                    _fail("configuration_conflict", "هوية المشروع أو مساحة العمل أو إعداد الجلسة تغيرت")
                try:
                    actions_fd = _open_directory(self.root / "actions")
                    try:
                        if _read(actions_fd, "manifest.json", MAX_STATE_BYTES) is None:
                            _fail("state_missing", "بيان مخزن الأفعال مفقود")
                    finally:
                        os.close(actions_fd)
                except FileNotFoundError:
                    _fail("state_missing", "مخزن أفعال جلسة سابقة مفقود")
            self.action_store = ActionStore(self.root / "actions", self.workspace)
            self._settle_stops(self._load())
            self.context = ToolContext(self.workspace, self.journal,
                                       allowed_consents=frozenset({"auto", "logged"}))

    @staticmethod
    def _private_dir(fd):
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            _fail("unsafe_permissions", "دليل التحكم خاص بمالكه")

    def _check(self):
        if self._fd is None:
            _fail("session_lock_required", "قفل الجلسة مطلوب")
        for path, expected in ((self.root, self._root_identity),
                               (self.workspace, self._workspace_identity)):
            fd = _open_directory(path)
            try:
                if _identity(os.fstat(fd)) != expected:
                    _fail("workspace_changed", "تغيرت هوية مساحة العمل أو دليل الجلسة")
            finally:
                os.close(fd)
        self._private_dir(self._fd)
        lock = os.stat("session.lock", dir_fd=self._fd, follow_symlinks=False)
        _regular(lock)
        if lock.st_mode & 0o077 or _identity(lock) != self._lock_identity:
            _fail("session_lock_changed", "استُبدل قفل الجلسة")

    @contextmanager
    def _lock(self):
        if not self._thread_lock.acquire(blocking=False):
            _fail("session_busy", "الجلسة قيد التنفيذ")
        root_fd = lock_fd = None
        try:
            root_fd = _open_directory(self.root)
            if _identity(os.fstat(root_fd)) != self._root_identity:
                _fail("workspace_changed", "استُبدل دليل الجلسة")
            self._private_dir(root_fd)
            lock_fd = os.open("session.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                              0o600, dir_fd=root_fd)
            _regular(os.fstat(lock_fd))
            try:
                filelock.lock(lock_fd, blocking=False)
            except BlockingIOError:
                _fail("session_busy", "الجلسة تعمل في عملية أخرى")
            self._fd, self._lock_identity = root_fd, _identity(os.fstat(lock_fd))
            self._check()
            yield
        except (OSError, JournalRefused) as exc:
            _fail(getattr(exc, "code", "unsafe_path"), "تعذر التحقق من ملفات التحكم")
        finally:
            self._fd = None
            if lock_fd is not None:
                os.close(lock_fd)
            if root_fd is not None:
                os.close(root_fd)
            self._thread_lock.release()

    def _raw(self, name, limit=MAX_STATE_BYTES):
        self._check()
        found = _read(self._fd, name, limit)
        if found is None:
            return None
        if os.stat(name, dir_fd=self._fd, follow_symlinks=False).st_mode & 0o077:
            _fail("unsafe_permissions", "ملفات التحكم خاصة بمالكها")
        return found[0]

    def _write(self, name, value):
        self._check()
        self._raw(name)
        raw = canonical_bytes(value)
        if len(raw) > MAX_STATE_BYTES:
            _fail("state_limit", "حالة الجلسة تجاوزت الحد")
        temporary = "write-" + uuid.uuid4().hex + ".tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self._fd)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(fd)
            self._check()
            os.replace(temporary, name, src_dir_fd=self._fd, dst_dir_fd=self._fd)
            os.fsync(self._fd)
        finally:
            os.close(fd)
            if temporary in os.listdir(self._fd):
                os.unlink(temporary, dir_fd=self._fd)

    def _save(self, state):
        entries = self.ledger.entries()
        state["ledger"] = {"count": len(entries), "head": entries[-1]["digest"] if entries else GENESIS}
        self._write("state.json", {"state": state, "sha256": digest(state)})

    def _request(self, messages, thinking=False):
        return validated(Request(messages, self.config["model"], self.config["model_version"],
                                 self.config["max_output"], float(self.config["deadline_s"]), "local_only", None,
                                 tuple(ToolSpec(**spec) for spec in self.config["tools"]), thinking=thinking))

    def _load(self):
        try:
            manifest = self._raw("manifest.json")
            raw = self._raw("state.json")
            if manifest is None or raw is None:
                _fail("state_missing", "حالة جلسة سابقة مفقودة")
            if _decode(manifest) != self.config:
                _fail("configuration_conflict", "هوية المشروع أو مساحة العمل أو إعداد الجلسة تغيرت")
            if [spec.declared() for spec in self.registry.specs()] != self.config["tools"]:
                _fail("configuration_conflict", "تغير عقد الأدوات بعد تهيئة الجلسة")
            envelope = _decode(raw)
            state = envelope["state"]
            if set(envelope) != {"state", "sha256"} or digest(state) != envelope["sha256"]:
                _fail("state_corrupt", "بصمة حالة الجلسة لا تطابق")
            if (set(state) != {"schema_version", "config_digest", "turns", "ledger"}
                    or state["schema_version"] != 2 or state["config_digest"] != digest(self.config)
                    or not isinstance(state["turns"], list) or len(state["turns"]) > self.config["max_turns"]
):
                _fail("state_corrupt", "بنية حالة الجلسة غير صالحة")
            self.ledger.verify_chain()
            entries = self.ledger.entries()
            checkpoint = state["ledger"]
            count = checkpoint["count"]
            if (type(count) is not int or not 0 <= count <= len(entries)
                    or checkpoint["head"] != (entries[count - 1]["digest"] if count else GENESIS)):
                _fail("ledger_binding_invalid", "السجل لا يطابق رأس الحالة المحفوظ")
            seen, intents = set(), {}
            previous = [_message_payload(Message("system", self.config["system"]))]
            unresolved = False
            for turn in state["turns"]:
                # طلبُ التفكير مفتاحٌ اختياريّ قيمتُه True وحدها (ك٤٧)، فحالاتُ ما قبله صالحة كما هي
                thinking = turn.get("thinking", False)
                if (set(turn) - {"thinking"} != _TURN_KEYS or ("thinking" in turn and thinking is not True)
                        or not _id(turn["turn_id"]) or turn["turn_id"] in seen or not _text(turn["text"])
                        or unresolved or turn["initial_messages"] != previous
                        or turn["input_digest"] != self._input_digest(turn)
                        or not isinstance(turn["calls"], list) or len(turn["calls"]) > self.config["max_steps"]):
                    _fail("state_corrupt", "مدخلات الجولة أو ترتيبها غير صالح")
                seen.add(turn["turn_id"])
                self._request(_messages(turn["initial_messages"]) + (_user_message(turn["text"]),), thinking)
                for intent in turn["calls"]:
                    payload = intent["request"]
                    request = self._request(_messages(payload["messages"]), thinking)
                    expected_key = "agent-" + digest({"request": payload, "session_id": self.session_id,
                                                      "turn_id": turn["turn_id"]})
                    prefix = turn["initial_messages"] + [_message_payload(_user_message(turn["text"]))]
                    if (set(intent) != {"request", "request_digest", "idempotency_key"}
                            or payload != request.fingerprint_payload() or digest(payload) != intent["request_digest"]
                            or payload["messages"][:len(prefix)] != prefix
                            or intent["idempotency_key"] != expected_key or expected_key in intents):
                        _fail("state_corrupt", "بصمة نداء الجولة لا تطابق مدخلاته")
                    intents[expected_key] = intent
                result = turn["result"]
                if result is not None:
                    self._verify_result(turn, entries)
                    if result["status"] == "complete":
                        previous = turn["transcript"]
                        self._request(_messages(previous))
                    unresolved = result["status"] not in TERMINAL
                else:
                    unresolved = True
            for entry in entries:
                record = entry["record"]
                intent = intents.get(record.get("idempotency_key"))
                if intent is None or record.get("request_digest") != intent["request_digest"]:
                    _fail("ledger_binding_invalid", "قيد نداء لا ينتمي إلى مدخلات محفوظة")
            return state
        except (KeyError, TypeError, ValueError, AttributeError, LedgerCorrupt) as exc:
            if isinstance(exc, ConversationError):
                raise
            _fail("state_corrupt", "فشل التحقق من حالة الجلسة أو سجلها")

    def _input_digest(self, turn):
        return digest({"config": digest(self.config), "turn_id": turn["turn_id"],
                       "text": turn["text"], "initial_messages": turn["initial_messages"],
                       **({"thinking": True} if turn.get("thinking") else {})})

    def _verify_result(self, turn, entries):
        result = turn["result"]
        if (set(result) != {"turn_id", "content", "status", "error_code", "steps", "pending"}
                or result["turn_id"] != turn["turn_id"] or not isinstance(result["content"], str)
                or result["status"] not in TERMINAL | {"awaiting_owner", "outcome_unknown"}
                or not isinstance(result["steps"], list) or not isinstance(result["pending"], list)):
            _fail("state_corrupt", "نتيجة الجولة غير صالحة")
        by_digest = {entry["digest"]: entry["record"] for entry in entries}
        intents = {item["idempotency_key"]: item for item in turn["calls"]}
        messages = list(_messages(turn["initial_messages"])) + [_user_message(turn["text"])]
        for index, step in enumerate(result["steps"]):
            record = by_digest.get(step["ledger_digest"])
            if (record is None or step["index"] != index
                    or step["request_digest"] != record["request_digest"]
                    or record["idempotency_key"] not in intents
                    or record["request_digest"] != digest(self._request(
                        tuple(messages), turn.get("thinking", False)).fingerprint_payload())):
                _fail("ledger_binding_invalid", "خطوة محفوظة بلا قيد ومدخل مطابقين")
            response = record.get("response")
            if response is None:
                if step["content"] or step["tool_calls"] or step["tool_results"]:
                    _fail("ledger_binding_invalid", "محتوى بلا جواب مثبت")
                continue
            if (step["content"] != response["content"] or step["stop_reason"] != response["stop_reason"]
                    or step["tool_calls"] != response.get("tool_calls", [])
                    or step.get("thinking", "") != response.get("thinking", "")):
                _fail("ledger_binding_invalid", "جواب الخطوة لا يطابق السجل")
            if step["stop_reason"] != "complete":
                if step["tool_results"]:
                    _fail("state_corrupt", "أثر محفوظ بعد جواب غير مكتمل")
                continue
            calls = tuple(ToolCall(**call) for call in step["tool_calls"])
            messages.append(Message("assistant", step["content"], tool_calls=calls))
            if len(step["tool_results"]) > len(calls):
                _fail("state_corrupt", "نتائج أدوات زائدة")
            for call_index, tool_result in enumerate(step["tool_results"]):
                call = calls[call_index]
                if tool_result["call_id"] != call.call_id or tool_result["name"] != call.name:
                    _fail("state_corrupt", "نتيجة أداة لا تطابق نداءها")
                if "action_id" in tool_result:
                    view = self.action_store.get(tool_result["action_id"])
                    expected = {"session_id": self.session_id, "turn_id": turn["turn_id"],
                                "step_index": index, "call_index": call_index}
                    if view["position"] != expected or view["call_digest"] != tool_result["call_digest"]:
                        _fail("action_binding_conflict", "نتيجة أداة من موضع آخر")
                    if view["state"] == "completed" and tool_result["status"] != "awaiting_owner":
                        if self.action_store.completed_result(view["action_id"]) != tool_result:
                            _fail("action_binding_conflict", "نتيجة أداة تختلف عن إيصالها")
                if tool_result["status"] in {"awaiting_owner", "outcome_unknown"}:
                    break
                if tool_result["status"] == "refused" and "action_id" not in tool_result:
                    break
                messages.append(_result_message(tool_result))
        if result["status"] == "complete":
            if (not result["steps"]
                    or by_digest[result["steps"][-1]["ledger_digest"]].get("response") is None
                    or result["steps"][-1]["tool_calls"]
                    or result["steps"][-1]["stop_reason"] != "complete"
                    or result["content"] != result["steps"][-1]["content"]
                    or turn["transcript"] != [_message_payload(message) for message in messages]):
                _fail("state_corrupt", "اكتمال أو تاريخ لا يطابق السجل وإيصالات الأدوات")

    def _find(self, state, turn_id):
        if not _id(turn_id):
            _fail("turn_id_invalid", "هوية جولة صالحة مطلوبة")
        return next((turn for turn in state["turns"] if turn["turn_id"] == turn_id), None)

    def _pending(self, turn):
        return [view for view in self.action_store.pending()
                if view["position"]["session_id"] == self.session_id
                and view["position"]["turn_id"] == turn["turn_id"]]

    def _uncertain(self, turn):
        pending = self._pending(turn)
        code = "action_outcome_unknown" if any(v["state"] == "outcome_unknown" for v in pending) else "provider_outcome_unknown"
        return {"turn_id": turn["turn_id"], "content": "", "status": "outcome_unknown",
                "error_code": code, "steps": [], "pending": pending}

    def _public(self, turn):
        result = _copy(turn["result"] or self._uncertain(turn))
        if result["status"] in {"awaiting_owner", "outcome_unknown"}:
            result["pending"] = self._pending(turn)
        return result

    def _can_replay(self, turn):
        records = {entry["record"]["idempotency_key"]: entry["record"] for entry in self.ledger.entries()}
        for intent in turn["calls"]:
            record = records.get(intent["idempotency_key"])
            if record is None or record.get("error_code") == "aborted_unclassified":
                return False
        return not any(view["state"] == "outcome_unknown" for view in self._pending(turn))

    def _admit_turn(self, state, turn_id, text, thinking=False):
        if not _id(turn_id) or not _text(text):
            _fail("turn_input_invalid", "معرف جولة ونص UTF-8 غير فارغ مطلوبان")
        if not isinstance(thinking, bool):
            _fail("turn_input_invalid", "طلب التفكير True أو False")
        existing = self._find(state, turn_id)
        if existing is not None:
            if existing["text"] != text or existing.get("thinking", False) != thinking:
                _fail("turn_id_conflict", "معرف الجولة مرتبط بنص أو طلب تفكير مختلف")
            return existing, None
        if len(state["turns"]) >= self.config["max_turns"]:
            _fail("turn_limit", "بلغت الجلسة حد الجولات")
        if any(turn["result"] is None or turn["result"]["status"] not in TERMINAL for turn in state["turns"]):
            _fail("turn_unresolved", "توجد جولة غير محسومة؛ استأنفها أولًا")
        complete = [turn for turn in state["turns"] if turn["result"]["status"] == "complete"]
        initial = complete[-1]["transcript"] if complete else [_message_payload(Message("system", self.config["system"]))]
        request = self._request(_messages(initial) + (_user_message(text),), thinking)
        if len(canonical_bytes(request.fingerprint_payload()).decode("utf-8")) > self.config["max_context_chars"]:
            _fail("context_limit", "سياق الجلسة تجاوز الحد؛ أنشئ جلسة جديدة")
        return None, initial

    def _stopped_steps(self, turn):
        """Restore evidence only: no provider, registry invocation or new receipt.

        A crash can leave completed tool receipts before the turn summary. Keep
        their action IDs visible after cancellation so the owner can inspect or
        revert their effects. Missing/unstarted calls never become effects.
        """
        by_key = {entry["record"]["idempotency_key"]: entry for entry in self.ledger.entries()}
        steps = []
        for index, intent in enumerate(turn["calls"]):
            entry = by_key.get(intent["idempotency_key"])
            if entry is None:
                _fail("ledger_binding_invalid", "نداء محفوظ بلا نتيجة مثبتة")
            record = entry["record"]
            response = record.get("response") or {}
            calls = tuple(ToolCall(**call) for call in response.get("tool_calls", []))
            step = {"index": index, "content": response.get("content", ""),
                    "request_digest": record["request_digest"], "ledger_digest": entry["digest"],
                    "replayed": True, "tool_results": [], "stop_reason": response.get("stop_reason", "complete"),
                    "tool_calls": [call.declared() for call in calls],
                    **({"thinking": response["thinking"]} if response.get("thinking") else {})}
            steps.append(step)
            if not calls or step["stop_reason"] != "complete":
                break
            try:
                self.action_store.register_step(session_id=self.session_id, turn_id=turn["turn_id"],
                    step_index=index, request_digest=record["request_digest"], calls=calls,
                    specs=self.registry.specs(), allow_new=False)
                plan_exists = True
            except ActionRefused as exc:
                if exc.code != "action_receipt_missing":
                    raise
                plan_exists = False
            for call_index, call in enumerate(calls):
                position = {"session_id": self.session_id, "turn_id": turn["turn_id"],
                            "step_index": index, "call_index": call_index}
                # This is the ActionStore position identity, not a supplied ID.
                action_id = "action-" + digest(position)
                try:
                    view = self.action_store.get(action_id)
                except ActionRefused as exc:
                    if exc.code != "action_receipt_missing":
                        raise
                    break
                if (not plan_exists or view["position"] != position or view["call_id"] != call.call_id
                        or view["name"] != call.name or view["arguments"] != call.arguments):
                    _fail("action_binding_conflict", "إيصال العرض لا يطابق خطة الجولة")
                if view["state"] != "completed":
                    break
                step["tool_results"].append(self.action_store.completed_result(action_id))
            if len(step["tool_results"]) != len(calls):
                break
        return steps

    def _settle_stop(self, state, turn):
        result = turn["result"]
        if result is not None and result["status"] in TERMINAL | {"outcome_unknown"}:
            return False
        if not self._stop_signals.requested(turn):
            return False
        if not self._can_replay(turn):
            turn["result"] = self._uncertain(turn)
        else:
            previous = result or {}
            turn["result"] = {"turn_id": turn["turn_id"], "content": previous.get("content", ""),
                              "status": "cancelled", "error_code": "stop_requested",
                              "steps": self._stopped_steps(turn), "pending": []}
        self._save(state)
        return True

    def _settle_stops(self, state):
        for turn in state["turns"]:
            self._settle_stop(state, turn)

    def request_stop(self, turn_id):
        """Stop follow-up at a safe boundary; do not kill or undo running work.

        Publishing uses an independent descriptor, so a running same-object
        thread keeps ownership of self._fd. Unknown turns raise turn_unknown;
        the HTTP layer may map the pre-admission race to stop_not_ready.
        """
        signal = self._stop_signals.request(turn_id, TERMINAL)
        if signal["status"] == "not_running":
            if signal["turn_status"] == "cancelled":
                return {"turn_id": turn_id, "status": "cancelled", "error_code": "stop_requested"}
            return {"turn_id": turn_id, "status": "not_running",
                    "turn_status": signal["turn_status"], "error_code": None}
        try:
            with self._lock():
                state = self._load()
                turn = self._find(state, turn_id)
                if turn is None:
                    _fail("turn_unknown", "الجولة غير موجودة")
                self._settle_stop(state, turn)
                current = self._public(turn)
                if current["status"] in {"cancelled", "outcome_unknown"}:
                    return {"turn_id": turn_id, "status": current["status"],
                            "error_code": current["error_code"]}
                if current["status"] in TERMINAL:
                    return {"turn_id": turn_id, "status": "not_running",
                            "turn_status": current["status"], "error_code": None}
        except ConversationError as exc:
            if exc.code != "session_busy":
                raise
        return {"turn_id": turn_id, "status": signal["status"],
                "error_code": signal.get("error_code")}

    def validate_turn(self, turn_id, text, thinking=False):
        """Read-only admission for trusted input staging; start_turn checks again.

        This is not a reservation. The application must serialize admission and
        staging; the session still refuses conflicting or changed state at start.
        """
        with self._lock():
            state = self._load()
            existing, _ = self._admit_turn(state, turn_id, text, thinking)
            return ({"status": "replayed", "result": self._public(existing)} if existing is not None
                    else {"status": "ready"})

    def start_turn(self, turn_id, text, provider, *, thinking=False):
        with self._lock():
            state = self._load()
            self._settle_stops(state)
            existing, initial = self._admit_turn(state, turn_id, text, thinking)
            if existing is not None:
                return self._public(existing)
            if getattr(provider, "is_local", None) is not True:
                _fail("policy_requires_local", "الجلسة تتطلب مزودًا محليًا")
            turn = {"turn_id": turn_id, "text": text, "initial_messages": _copy(initial),
                    "input_digest": "", "calls": [], "result": None, "transcript": [],
                    # يُحفظ حين يُطلب فقط (ك٤٧): جولاتُ ما قبله بلا مفتاح
                    **({"thinking": True} if thinking else {})}
            turn["input_digest"] = self._input_digest(turn)
            state["turns"].append(turn)
            self._save(state)
            return self._run(state, turn, provider)

    def resume(self, turn_id, provider):
        with self._lock():
            state = self._load()
            turn = self._find(state, turn_id)
            if turn is None:
                _fail("turn_unknown", "الجولة غير موجودة")
            self._settle_stop(state, turn)
            if turn["result"] is not None and (turn["result"]["status"] in TERMINAL
                    or (turn["result"]["status"] == "outcome_unknown" and self._stop_signals.requested(turn))):
                return self._public(turn)
            if not self._can_replay(turn):
                turn["result"] = self._uncertain(turn)
                self._save(state)
                return self._public(turn)
            if getattr(provider, "is_local", None) is not True:
                _fail("policy_requires_local", "الجلسة تتطلب مزودًا محليًا")
            return self._run(state, turn, provider)

    def _run(self, state, turn, provider):
        try:
            run = run_agent(_user_message(turn["text"]).content, _ProviderGuard(self, state, turn, provider),
                            self.registry, self.context,
                            ledger=self.ledger, budget=Budget(0, 0), model=self.config["model"],
                            model_version=self.config["model_version"], max_steps=self.config["max_steps"],
                            max_output=self.config["max_output"], deadline_s=float(self.config["deadline_s"]),
                            action_store=self.action_store, session_id=self.session_id, turn_id=turn["turn_id"],
                            initial_messages=_messages(turn["initial_messages"]),
                            stop_check=lambda: self._stop_signals.requested(turn),
                            thinking=turn.get("thinking", False))
            turn["result"] = {"turn_id": turn["turn_id"], "content": run.answer, "status": run.status,
                              "error_code": run.code, "steps": [{"index": step.index, "content": step.content,
                                  "request_digest": step.request_digest, "ledger_digest": step.ledger_digest,
                                  "replayed": step.replayed, "tool_results": _copy(list(step.tool_results)),
                                  "stop_reason": step.stop_reason,
                                  "tool_calls": [call.declared() for call in step.tool_calls],
                                  **({"thinking": step.thinking} if step.thinking else {})} for step in run.steps],
                              "pending": [] if run.status == "cancelled" else self._pending(turn)}
            turn["transcript"] = [_message_payload(message) for message in run.transcript]
            if not self._can_replay(turn):
                turn["result"]["status"] = "outcome_unknown"
                turn["result"]["error_code"] = self._uncertain(turn)["error_code"]
            self._save(state)
            return self._public(turn)
        except BaseException as exc:
            # No exception text, provider prompt, or private path is made public.
            # A persisted intent protects even a failure before core's finally.
            turn["result"] = self._uncertain(turn)
            self._save(state)
            if isinstance(exc, (KeyboardInterrupt, SystemExit, ConversationError)):
                raise
            return self._public(turn)

    def decide(self, action_id, expected_call_digest, approved, expected_revision):
        with self._lock():
            state = self._load()
            self._settle_stops(state)
            view = self._owned_action(state, action_id)
            if not any(t["turn_id"] == view["position"]["turn_id"] and t["result"] is not None
                       and t["result"]["status"] == "awaiting_owner" for t in state["turns"]):
                _fail("action_not_pending", "الجولة لا تنتظر قرارًا لهذا الفعل")
            return self.action_store.decide(action_id, expected_call_digest, expected_revision, approve=approved)

    def _owned_action(self, state, action_id):
        view = self.action_store.get(action_id)
        if (view["position"]["session_id"] != self.session_id
                or self._find(state, view["position"]["turn_id"]) is None):
            _fail("action_identity_conflict", "الفعل لا ينتمي إلى هذه الجلسة")
        return view

    def revert(self, action_id, request_id):
        from agent.action_revert import revert_prepared
        if not _id(request_id):
            _fail("request_id_invalid", "هوية طلب رجوع مطلوبة")
        with self._lock():
            state = self._load()
            self._owned_action(state, action_id)
            result = revert_prepared(self.action_store, self.context, action_id,
                                     session_id=self.session_id, request_id=request_id)
            public = {"status": result["status"], "error_code": result.get("code"),
                      "action_id": action_id, "request_id": request_id,
                      "receipt_action_id": result.get("action_id")}
            if result["status"] == "ok":
                outcome = json.loads(result["content"])
                public.update(status=outcome["status"], result=outcome["result"])
            return public

    def history(self, recover=False):
        if type(recover) is not bool:
            _fail("recover_invalid", "خيار الاستعادة منطقي")
        with self._lock():
            state = self._load()
            if recover:
                self._settle_stops(state)
                changed = False
                for turn in state["turns"]:
                    if turn["result"] is None:
                        turn["result"] = self._uncertain(turn)
                        changed = True
                if changed:
                    self._save(state)
            return {"schema_version": 2, "session_id": self.session_id, "project_id": self.project_id,
                    "workspace_root": str(self.workspace),
                    "turns": [{"turn_id": turn["turn_id"], "text": turn["text"], "result": self._public(turn),
                               **({"thinking": True} if turn.get("thinking") else {})}
                              for turn in state["turns"]],
                    "pending": [view for view in self.action_store.pending()
                                if any(turn["turn_id"] == view["position"]["turn_id"]
                                       and (turn["result"] is None or turn["result"]["status"] not in TERMINAL)
                                       for turn in state["turns"])]}
