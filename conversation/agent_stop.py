"""Durable cooperative stop signals; no shared execution-lock descriptor.

The owner controls this private directory. Bindings detect corruption and copied
markers; they are not authentication against an owner rewriting all state.
"""
from contextlib import contextmanager
import os
import uuid

from agent.journal import JournalRefused, _identity, _open_directory, _read
from conversation.session import ConversationError, _decode, _fail, _id
from core.canonical import canonical_bytes, digest

LIMIT = 32 * 1024 * 1024


class StopSignals:
    def __init__(self, root, workspace, root_identity, workspace_identity, config):
        self.root, self.workspace = root, workspace
        self.root_identity, self.workspace_identity = root_identity, workspace_identity
        self.config = config

    def _check(self, fd):
        for path, expected in ((self.root, self.root_identity),
                               (self.workspace, self.workspace_identity)):
            opened = _open_directory(path)
            try:
                if _identity(os.fstat(opened)) != expected:
                    _fail("workspace_changed", "تغير دليل الجلسة أو مساحة العمل")
            finally:
                os.close(opened)
        info = os.fstat(fd)
        if (_identity(info) != self.root_identity or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            _fail("unsafe_permissions", "دليل إشارة الإيقاف خاص وثابت")

    @contextmanager
    def _directory(self):
        fd = None
        try:
            fd = _open_directory(self.root)
            self._check(fd)
            if self._value(fd, "manifest.json") != self.config:
                _fail("configuration_conflict", "بيان الجلسة لا يطابق إشارة الإيقاف")
            yield fd
            self._check(fd)
        except (OSError, JournalRefused) as exc:
            _fail(getattr(exc, "code", "unsafe_path"), "تعذر التحقق من إشارة الإيقاف")
        finally:
            if fd is not None:
                os.close(fd)

    def _value(self, fd, name):
        # The session publishes state atomically while holding its own lock.
        # A bounded re-read handles an observed replacement, never redoes work.
        for attempt in range(3):
            try:
                found = _read(fd, name, LIMIT)
                break
            except JournalRefused as exc:
                if exc.code != "file_changed" or attempt == 2:
                    raise
        if found is None:
            return None
        if os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode & 0o077:
            _fail("unsafe_permissions", "إشارة الإيقاف وحالة الجلسة خاصتان")
        return _decode(found[0])

    def _binding(self, turn):
        return {"schema_version": 1, "session_id": self.config["session_id"],
                "project_id": self.config["project_id"], "config_digest": digest(self.config),
                "control_identity": [str(n) for n in self.root_identity],
                "turn_id": turn["turn_id"], "input_digest": turn["input_digest"]}

    @staticmethod
    def _name(turn_id):
        if not _id(turn_id):
            _fail("turn_id_invalid", "هوية جولة صالحة مطلوبة")
        return "stop-" + digest(turn_id) + ".json"

    def _read_marker(self, fd, turn):
        value = self._value(fd, self._name(turn["turn_id"]))
        if value is None:
            return False
        binding = self._binding(turn)
        if value != {"binding": binding, "sha256": digest(binding)}:
            _fail("stop_binding_conflict", "إشارة الإيقاف لا تنتمي إلى هذه الجولة")
        return True

    def requested(self, turn):
        with self._directory() as fd:
            return self._read_marker(fd, turn)

    def request(self, turn_id, terminal):
        """Read an atomic state snapshot and publish without the session lock."""
        name = self._name(turn_id)
        with self._directory() as fd:
            envelope = self._value(fd, "state.json")
            try:
                state = envelope["state"]
                if (set(envelope) != {"state", "sha256"} or envelope["sha256"] != digest(state)
                        or state["config_digest"] != digest(self.config)
                        or state["schema_version"] != 2):
                    _fail("state_corrupt", "حالة الجولة لا تطابق بصمتها")
                turn = next((t for t in state["turns"] if t["turn_id"] == turn_id), None)
                if turn is None:
                    _fail("turn_unknown", "الجولة غير موجودة")
                expected = digest({"config": digest(self.config), "turn_id": turn_id,
                                   "text": turn["text"], "initial_messages": turn["initial_messages"]})
                if turn["input_digest"] != expected:
                    _fail("state_corrupt", "مدخل الجولة لا يطابق بصمته")
                result = turn["result"]
                status = result["status"] if result else None
            except (KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, ConversationError):
                    raise
                _fail("state_corrupt", "تعذر قراءة حالة الجولة للإيقاف")
            if status in terminal:
                return {"turn_id": turn_id, "status": "not_running", "turn_status": status,
                        "stop_requested": status == "cancelled"}
            if not self._read_marker(fd, turn):
                binding = self._binding(turn)
                raw = canonical_bytes({"binding": binding, "sha256": digest(binding)})
                temporary = "stop-write-" + uuid.uuid4().hex + ".tmp"
                opened = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=fd)
                try:
                    with os.fdopen(opened, "wb", closefd=False) as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(opened)
                    self._check(fd)
                    # Every permitted concurrent writer has exactly this binding.
                    # Atomic replacement avoids a briefly two-linked file which
                    # the no-hardlink reader would correctly reject.
                    os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                finally:
                    os.close(opened)
                    try:
                        os.unlink(temporary, dir_fd=fd)
                    except FileNotFoundError:
                        pass
                os.fsync(fd)
                self._read_marker(fd, turn)
            return {"turn_id": turn_id,
                    "status": "outcome_unknown" if status == "outcome_unknown" else "stop_requested",
                    "turn_status": status, "stop_requested": True,
                    "error_code": result.get("error_code") if status == "outcome_unknown" else None}
