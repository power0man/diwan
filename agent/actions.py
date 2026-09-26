"""Durable, private action receipts and single-use owner decisions.

This serializes cooperating application processes, not a hostile host owner.
``started`` is fsynced before calling a handler. A process loss in the gap before
``completed`` leaves an unknown outcome: it is never permission to retry. The
workspace manifest detects changes before execution but is not a filesystem
transaction or an exactly-once guarantee. Container inputs are frozen bytes.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from agent.journal import (Journal, JournalRefused, _exact, _identity, _open_directory,
                           _read, _regular, _stamp)
from core import filelock
from core.canonical import PayloadRejected, canonical_bytes, digest
from core.contracts import CALL_ID, CONSENT_GRADES, TOOL_NAME, ToolCall, ToolSpec

SHA = re.compile(r"[a-f0-9]{64}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
ACTION_ID = re.compile(r"action-[a-f0-9]{64}\Z")
WORKSPACE_ID = re.compile(r"[a-f0-9]{32}\Z")
MAX_RECORD_BYTES = 96 * 1024 * 1024
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_FILES = 4096
SKIP = {".git", ".diwan-journal", "__pycache__", "node_modules", ".venv", "var"}


class ActionRefused(PayloadRejected):
    def __init__(self, code, reason):
        super().__init__("action", code, reason)


def _fail(code, reason):
    raise ActionRefused(code, reason)


def _path(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        _fail("action_path_invalid", "مسار مطلق بلا رجوع مطلوب")
    return path


def _copy(value):
    return json.loads(canonical_bytes(value))


def _position(session_id, turn_id, step_index, call_index=None):
    if any(not isinstance(x, str) or not IDENTIFIER.fullmatch(x) for x in (session_id, turn_id)):
        _fail("action_identity_invalid", "هوية جلسة ودور نصية محدودة مطلوبة")
    if type(step_index) is not int or not 0 <= step_index < 64:
        _fail("action_identity_invalid", "موضع خطوة غير صالح")
    position = {"session_id": session_id, "turn_id": turn_id, "step_index": step_index}
    if call_index is not None:
        if type(call_index) is not int or not 0 <= call_index < 128:
            _fail("action_identity_invalid", "موضع نداء غير صالح")
        position["call_index"] = call_index
    return position


def _declaration(call, spec):
    if (not isinstance(call, ToolCall) or not isinstance(spec, ToolSpec)
            or not isinstance(call.call_id, str) or not CALL_ID.fullmatch(call.call_id)
            or not isinstance(call.name, str) or not TOOL_NAME.fullmatch(call.name)
            or call.name != spec.name or not isinstance(call.arguments, dict)
            or spec.consent not in CONSENT_GRADES or not isinstance(spec.parameters, dict)
            or type(spec.reversible) is not bool
            or (spec.consent == "logged" and not spec.reversible)):
        _fail("action_call_invalid", "نداء أو عقد أداة غير صالح")
    return _copy({"call": call.declared(), "spec": spec.declared()})


class ActionStore:
    """One private store bound to one workspace; no model-selected state paths.

    The persisted binding is either the workspace's location-independent identity
    (``workspace_id``, ج١٢) or, for stores created before it, its path, device and
    inode. Path and inode are always re-checked at runtime (``_check_paths``); with
    ``workspace_id`` they only stay out of the receipts, so a backup restores intact.
    """

    def __init__(self, state_root, workspace_root, *, workspace_id=None):
        if workspace_id is not None and (not isinstance(workspace_id, str)
                                         or not WORKSPACE_ID.fullmatch(workspace_id)):
            _fail("action_identity_invalid", "هوية مساحة العمل غير صالحة")
        self.workspace_id = workspace_id
        self.directory, self.workspace = _path(state_root), _path(workspace_root)
        if self.directory == self.workspace or self.directory.is_relative_to(self.workspace):
            _fail("action_state_inside_workspace", "مخزن الأفعال يجب أن يكون خارج مساحة الأدوات")
        try:
            work_fd = _open_directory(self.workspace)
            try:
                self.workspace_identity = _identity(os.fstat(work_fd))
            finally:
                os.close(work_fd)
            fd = _open_directory(self.directory, create=True)
            try:
                info = os.fstat(fd)
                if info.st_uid != os.getuid() or info.st_mode & 0o077:
                    _fail("action_state_not_private", "مخزن الأفعال يجب أن يكون خاصًا بالمستخدم")
                self.directory_identity = _identity(info)
            finally:
                os.close(fd)
            self.manifest = {"schema_version": 1, "workspace": self._workspace_binding()}
            with self._operation() as fd:
                existing = self._load(fd, "manifest.json", missing=True)
                if existing is None:
                    if set(os.listdir(fd)) - {"store.lock"}:
                        _fail("action_manifest_missing", "مخزن سابق بلا بيان؛ لا إعادة تهيئة")
                    self._save(fd, "manifest.json", self.manifest)
                elif existing != self.manifest:
                    _fail("action_workspace_changed", "المخزن مربوط بمساحة أخرى")
            self._initialized = True
        except (OSError, JournalRefused) as exc:
            raise ActionRefused("action_state_unsafe", "تعذر فتح مخزن آمن بلا روابط أو التباس") from exc

    def _workspace_binding(self):
        if self.workspace_id is not None:
            return {"workspace_id": self.workspace_id}
        return {"path": str(self.workspace), "device": str(self.workspace_identity[0]),
                "inode": str(self.workspace_identity[1])}

    def _check_paths(self, fd):
        for path, expected in ((self.directory, self.directory_identity),
                               (self.workspace, self.workspace_identity)):
            probe = _open_directory(path)
            try:
                if _identity(os.fstat(probe)) != expected:
                    _fail("action_workspace_changed", "تغيرت هوية مسار المخزن أو مساحة العمل")
            finally:
                os.close(probe)
        if _identity(os.fstat(fd)) != self.directory_identity:
            _fail("action_state_changed", "تغيرت هوية مخزن الأفعال")
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            _fail("action_state_not_private", "مخزن الأفعال لم يعد خاصًا")
        if getattr(self, "_active_lock", None) is not None:
            lock_fd = self._active_lock
            info = os.stat("store.lock", dir_fd=fd, follow_symlinks=False)
            if _identity(info) != _identity(os.fstat(lock_fd)) or info.st_nlink != 1:
                _fail("action_lock_changed", "تغير قفل مخزن الأفعال")

    @contextmanager
    def _operation(self):
        fd = lock_fd = None
        try:
            fd = _open_directory(self.directory)
            self._check_paths(fd)
            _exact(fd, "store.lock")
            lock_fd = os.open("store.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                              0o600, dir_fd=fd)
            info = os.fstat(lock_fd)
            _regular(info)
            if info.st_mode & 0o077:
                _fail("action_state_not_private", "قفل الأفعال ليس خاصًا")
            try:
                filelock.lock(lock_fd, blocking=False)
            except BlockingIOError:
                _fail("action_store_busy", "عملية أخرى تستخدم مخزن الأفعال")
            self._active_lock = lock_fd
            self._check_paths(fd)
            os.fsync(fd)
            has_manifest = _exact(fd, "manifest.json")
            if getattr(self, "_initialized", False) and not has_manifest:
                _fail("action_manifest_missing", "بيان مخزن الأفعال مفقود")
            if hasattr(self, "manifest") and has_manifest:
                if self._load(fd, "manifest.json") != self.manifest:
                    _fail("action_workspace_changed", "بيان مساحة العمل لا يطابق المخزن")
            yield fd
        except (JournalRefused, OSError) as exc:
            raise ActionRefused("action_state_unsafe", "مسار مخزن الأفعال أو ملفه غير آمن") from exc
        finally:
            if lock_fd is not None:
                # Only clear our lock: a failed concurrent acquisition must not
                # erase another operation's identity check on the same object.
                if getattr(self, "_active_lock", None) == lock_fd:
                    self._active_lock = None
                os.close(lock_fd)
            if fd is not None:
                os.close(fd)

    def _load(self, fd, name, *, missing=False):
        found = _read(fd, name, MAX_RECORD_BYTES)
        if found is None:
            if missing:
                return None
            _fail("action_receipt_missing", "إيصال الفعل مفقود؛ لا إعادة للأثر")
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if info.st_mode & 0o077:
            _fail("action_state_not_private", "إيصال الفعل ليس خاصًا")
        def pairs(items):
            out = {}
            for key, value in items:
                if key in out:
                    _fail("action_state_corrupt", "مفتاح JSON مكرر")
                out[key] = value
            return out
        try:
            envelope = json.loads(found[0], object_pairs_hook=pairs)
            if (not isinstance(envelope, dict) or set(envelope) != {"record", "sha256"}
                    or digest(envelope["record"]) != envelope["sha256"]):
                _fail("action_state_corrupt", "الإيصال لا يطابق بصمته")
            return envelope["record"]
        except (ValueError, UnicodeError, TypeError) as exc:
            if isinstance(exc, ActionRefused):
                raise
            raise ActionRefused("action_state_corrupt", "إيصال غير قابل للتحقق") from exc

    def _save(self, fd, name, value):
        self._check_paths(fd)
        before = _read(fd, name, MAX_RECORD_BYTES)
        raw = canonical_bytes({"record": value, "sha256": digest(value)})
        if len(raw) > MAX_RECORD_BYTES:
            _fail("action_state_too_large", "إيصال الأفعال تجاوز سقف الحجم")
        temporary = ".write-" + uuid.uuid4().hex
        target_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600, dir_fd=fd)
        try:
            with os.fdopen(target_fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self._check_paths(fd)
            if _read(fd, name, MAX_RECORD_BYTES) != before:
                _fail("action_state_changed", "تغير الإيصال أثناء حفظه")
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass

    def register_step(self, *, session_id, turn_id, step_index, request_digest,
                      calls, specs, allow_new=True):
        position = _position(session_id, turn_id, step_index)
        if not isinstance(request_digest, str) or not SHA.fullmatch(request_digest):
            _fail("action_request_invalid", "بصمة الطلب مطلوبة")
        if not isinstance(calls, tuple) or not 1 <= len(calls) <= 128:
            _fail("action_call_invalid", "قائمة نداءات محدودة مطلوبة")
        by_name = {s.name: s for s in specs}
        if len(by_name) != len(specs) or len({c.call_id for c in calls}) != len(calls):
            _fail("action_call_invalid", "تكرار عقد أو نداء")
        declarations = []
        for call in calls:
            if call.name not in by_name:
                _fail("action_call_invalid", "أداة غير معلنة")
            declarations.append(_declaration(call, by_name[call.name]))
        plan = {"position": position, "request_digest": request_digest,
                "workspace": self._workspace_binding(), "calls": declarations}
        filename = "step-" + digest(position) + ".json"
        with self._operation() as fd:
            prior = self._load(fd, filename, missing=True)
            if prior is None:
                if not allow_new:
                    _fail("action_receipt_missing", "رد نموذج معاد بلا خطة فعل محفوظة")
                self._save(fd, filename, {"plan": plan, "prepared": []})
            elif prior.get("plan") != plan:
                _fail("action_binding_conflict", "موضع الخطوة مربوط بطلب أو نداءات مختلفة")
        return digest(plan)

    def _snapshot(self, name):
        if name in {"run_command", "run_tests", "execute_isolated_command"}:
            from core.execution import freeze_execution_inputs
            return {"kind": "container", "inputs": freeze_execution_inputs(self.workspace)}
        files, directories, total = [], [], 0
        root_fd = _open_directory(self.workspace)
        try:
            if _identity(os.fstat(root_fd)) != self.workspace_identity:
                _fail("action_workspace_changed", "تغيرت مساحة العمل")
            def walk(fd, prefix=""):
                nonlocal total
                for leaf in sorted(os.listdir(fd)):
                    rel = prefix + leaf
                    info = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        if leaf.startswith(".") or leaf in SKIP:
                            continue
                        directories.append(rel)
                        if len(files) + len(directories) > MAX_FILES:
                            _fail("action_snapshot_too_large", "لقطة مساحة العمل تتجاوز حد العناصر")
                        child = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                        try:
                            walk(child, rel + "/")
                        finally:
                            os.close(child)
                    else:
                        found = _read(fd, leaf, MAX_INPUT_BYTES)
                        if found is None:
                            _fail("action_snapshot_changed", "تغير عنصر أثناء التقاط المدخلات")
                        raw = found[0]
                        total += len(raw)
                        files.append({"path": rel, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
                        if total > MAX_INPUT_BYTES or len(files) + len(directories) > MAX_FILES:
                            _fail("action_snapshot_too_large", "لقطة مساحة العمل تتجاوز الحد")
            walk(root_fd)
        finally:
            os.close(root_fd)
        return {"kind": "workspace_visible_files", "files": files, "directories": directories}

    def _record(self, fd, action_id):
        if not isinstance(action_id, str) or not ACTION_ID.fullmatch(action_id):
            _fail("action_identity_invalid", "معرف فعل غير صالح")
        record = self._load(fd, action_id + ".json")
        required = {"binding", "call_digest", "state", "revision", "decision", "result"}
        if not isinstance(record, dict) or set(record) != required:
            _fail("action_state_corrupt", "بنية إيصال الفعل غير صالحة")
        binding = record["binding"]
        if (not isinstance(binding, dict)
                or set(binding) != {"position", "request_digest", "workspace", "call", "spec", "input_snapshot"}
                or record["call_digest"] != digest(binding)
                or action_id != "action-" + digest(record["binding"]["position"])
                or record["binding"]["workspace"] != self._workspace_binding()
                or record["state"] not in {"prepared", "approved", "denied", "started", "completed"}
                or type(record["revision"]) is not int or record["revision"] < 1
                or record["decision"] not in (None, True, False)
                or (record["state"] == "completed") != isinstance(record["result"], dict)
                or (record["state"] != "completed" and record["result"] is not None)
                or (record["state"] == "approved" and record["decision"] is not True)
                or (record["state"] == "denied" and record["decision"] is not False)):
            _fail("action_state_corrupt", "بنية إيصال الفعل غير صالحة")
        return record

    def _view(self, action_id, record):
        binding = record["binding"]
        state = record["state"]
        return {"action_id": action_id, "call_digest": record["call_digest"],
                "revision": record["revision"], "state": "outcome_unknown" if state == "started" else state,
                "call_id": binding["call"]["call_id"], "name": binding["call"]["name"],
                "arguments": _copy(binding["call"]["arguments"]),
                "consent": binding["spec"]["consent"], "reversible": binding["spec"]["reversible"],
                "position": _copy(binding["position"]),
                "input_snapshot_sha256": digest(binding["input_snapshot"])}

    def get(self, action_id):
        with self._operation() as fd:
            return self._view(action_id, self._record(fd, action_id))

    def inputs(self, action_id):
        """Private owner-facing preview; never inserted into model messages."""
        with self._operation() as fd:
            return _copy(self._record(fd, action_id)["binding"]["input_snapshot"])

    def completed_result(self, action_id):
        """Return a detached saved result only after a completed receipt exists."""
        with self._operation() as fd:
            record = self._record(fd, action_id)
            if record["state"] != "completed":
                _fail("action_not_completed", "الفعل بلا نتيجة اكتمال مثبتة")
            return _copy(record["result"])

    def pending(self):
        with self._operation() as fd:
            result = []
            for name in sorted(os.listdir(fd)):
                if name.startswith("action-") and name.endswith(".json"):
                    record = self._record(fd, name[:-5])
                    if record["state"] in {"prepared", "approved", "started"}:
                        result.append(self._view(name[:-5], record))
            return result

    def decide(self, action_id, call_digest, expected_revision, *, approve):
        if type(approve) is not bool or type(expected_revision) is not int:
            _fail("action_decision_invalid", "قرار ومراجعة متوقعة مطلوبان")
        with self._operation() as fd:
            record = self._record(fd, action_id)
            if record["call_digest"] != call_digest:
                _fail("action_binding_conflict", "القرار لا يطابق بصمة الفعل")
            if record["revision"] != expected_revision:
                _fail("action_revision_conflict", "تغيرت مراجعة الفعل منذ عرضه")
            if record["state"] != "prepared":
                _fail("action_decision_consumed", "سبق اتخاذ قرار لهذا الفعل")
            record.update(state="approved" if approve else "denied", decision=approve,
                          revision=record["revision"] + 1)
            self._save(fd, action_id + ".json", record)
            return self._view(action_id, record)

    def invoke(self, call, spec, context, handler, *, session_id, turn_id, step_index,
               call_index, request_digest, allow_new=True):
        position = _position(session_id, turn_id, step_index, call_index)
        declaration = _declaration(call, spec)
        fixed = {"position": position, "request_digest": request_digest,
                 "workspace": self._workspace_binding(), **declaration}
        action_id = "action-" + digest(position)
        if (_path(context.root) != self.workspace or not isinstance(context.journal, Journal)
                or context.journal.root != self.workspace):
            _fail("action_workspace_changed", "سياق الأداة لا يطابق مساحة الإيصال")
        with self._operation() as fd:
            step_position = _position(session_id, turn_id, step_index)
            plan_name = "step-" + digest(step_position) + ".json"
            stored = self._load(fd, plan_name)
            plan = stored["plan"]
            if (plan["request_digest"] != request_digest or call_index >= len(plan["calls"])
                    or plan["calls"][call_index] != declaration):
                _fail("action_binding_conflict", "الفعل لا يطابق خطة الخطوة المحفوظة")
            record = self._load(fd, action_id + ".json", missing=True)
            if record is None:
                if action_id in stored["prepared"] or not allow_new:
                    _fail("action_receipt_missing", "إيصال فعل سابق مفقود؛ لا إعادة للأثر")
                snapshot = self._snapshot(call.name)
                binding = {**fixed, "input_snapshot": snapshot}
                record = {"binding": binding, "call_digest": digest(binding), "state": "prepared",
                          "revision": 1, "decision": None, "result": None}
                # A lost record after this intent marker must fail closed.
                stored["prepared"].append(action_id)
                self._save(fd, plan_name, stored)
                self._save(fd, action_id + ".json", record)
            else:
                record = self._record(fd, action_id)
                if {key: record["binding"][key] for key in fixed} != fixed:
                    _fail("action_binding_conflict", "إعادة موضع الفعل بحجج أو عقد مختلف")
            view = self._view(action_id, record)
            base = {key: view[key] for key in ("call_id", "name", "action_id", "call_digest", "revision")}
            if record["state"] == "completed":
                return _copy(record["result"])
            if record["state"] == "started":
                return {**base, "status": "outcome_unknown", "code": "action_outcome_unknown",
                        "content": "بدأ تنفيذ سابق بلا نتيجة محفوظة؛ لا إعادة آلية للأثر"}
            if record["state"] == "denied":
                return {**base, "status": "refused", "code": "action_denied", "content": "رُفض هذا الفعل"}
            if record["state"] != "approved" and spec.consent not in context.allowed_consents:
                return {**base, "status": "awaiting_owner", "code": "consent_required",
                        "consent": spec.consent, "content": "هذا الفعل ينتظر قرارًا مربوطًا ببصمته"}
            snapshot = record["binding"]["input_snapshot"]
            if snapshot["kind"] != "container" and self._snapshot(call.name) != snapshot:
                _fail("action_inputs_changed", "تغيرت المدخلات؛ لا ينفذ قرار على لقطة مختلفة")
            self._check_paths(fd)
            record.update(state="started", revision=record["revision"] + 1)
            self._save(fd, action_id + ".json", record)
            # BaseException deliberately escapes: a killed/crashed handler leaves
            # a started receipt and cannot be retried by a resumed invocation.
            try:
                if snapshot["kind"] == "container":
                    from core.execution import frozen_execution_inputs
                    with frozen_execution_inputs(self.workspace, snapshot["inputs"]):
                        result = handler()
                else:
                    result = handler()
                result = _copy(result)
                if "action_id" in result:
                    result["journal_action_id"] = result.pop("action_id")
                # فعلٌ يكتب ملفّاتٍ عدّة (ج٨) يُرجع عنه رجوعًا واحدًا
                if "action_ids" in result:
                    result["journal_action_ids"] = result.pop("action_ids")
                record.update(state="completed", revision=record["revision"] + 1)
                result.update(action_id=action_id, call_digest=record["call_digest"], revision=record["revision"])
                record["result"] = result
                self._save(fd, action_id + ".json", record)
                return _copy(result)
            except Exception:
                # The result or its durability was not established. In particular,
                # never call this a pre-effect refusal after invoking the handler.
                return {**base, "status": "outcome_unknown", "code": "action_outcome_unknown",
                        "content": "بدأ الفعل وتعذر تثبيت نتيجته؛ يلزم فحص الأثر بلا إعادة تلقائية"}
