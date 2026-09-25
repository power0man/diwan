"""قراءة نصوص مختارة واقتراح كتابة جديدة لا تطبق دون بصمة موافقة.

المسارات تفتح عبر مقابض مجلدات وO_NOFOLLOW، والهدف ينشأ بـO_EXCL.
البصمات تكشف الفساد؛ لا تمنع مالك الحساب من إعادة كتابة الملفات
والإيصالات معًا أو استعادة نسخة قديمة. لا تنفيذ أو شبكة أو حذف هنا.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
import uuid

from core import filelock
from core.canonical import canonical_bytes, digest

MAX_BYTES = 65536
MAX_PROPOSALS = 256
# JSON قد يضاعف محارف مثل tab وbackslash؛ يشمل الحد الحمولة والبيانات الوصفية.
MAX_STATE_BYTES = 64 * 1024 * 1024
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")
PROTECTED = frozenset({".git", ".codex", ".agents", ".diwan-tools"})
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class WorkspaceError(ValueError):
    def __init__(self, code, reason):
        super().__init__(f"{reason} [{code}]")
        self.code, self.reason = code, reason


def _fail(code, reason):
    raise WorkspaceError(code, reason)


@dataclass(frozen=True)
class FileDocument:
    relative_path: str
    sha256: str
    content: str
    size_bytes: int


def _relative(value, *, writing=False):
    if not isinstance(value, (str, Path)):
        _fail("path_invalid", "مسار نسبي نصي مطلوب")
    value = str(value)
    parts = value.split("/")
    if (not value or value.startswith("/") or "\\" in value or "\x00" in value
            or any(p in ("", ".", "..") for p in parts)):
        _fail("path_invalid", "لا مسار مطلق أو عبور أو مكونات فارغة")
    if any(p.casefold() in PROTECTED or (writing and p.startswith(".")) for p in parts):
        _fail("path_protected", "مسار داخلي محمي أو هدف مخفي")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError:
        _fail("path_invalid", "المسار ليس UTF-8 صالحًا")
    if len(encoded) > 4096 or any(
            unicodedata.category(c).startswith("C") for c in value):
        _fail("path_invalid", "محارف المسار أو طوله غير صالحة")
    return value


def _utf8(content, limit):
    if not isinstance(content, str):
        _fail("text_invalid", "محتوى نصي مطلوب")
    try:
        raw = content.encode("utf-8", "strict")
    except UnicodeError:
        _fail("text_invalid", "النص ليس UTF-8 صالحًا")
    if len(raw) > limit:
        _fail("file_too_large", "النص يتجاوز حد البايتات")
    # Cf مثل علامات اتجاه العربية ووصل الحروف نص مشروع يبقى كما هو.
    # العرض الطرفي مسؤولية الواجهة؛ لا ننفذ أي محتوى من الملف.
    if any(c not in "\n\r\t" and unicodedata.category(c) == "Cc" for c in content):
        _fail("text_invalid", "محارف تحكم أو محتوى ثنائي مرفوض")
    return raw


def _io_error(exc):
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        _fail("unsafe_path", "رابط رمزي أو مكون ليس مجلدًا")
    if exc.errno == errno.ENOENT:
        _fail("file_missing", "ملف أو مجلد غير موجود")
    _fail("filesystem_error", "تعذر الوصول الآمن إلى الملف")


def _root_path(value):
    path = Path(value).absolute()
    if ".." in path.parts:
        _fail("path_invalid", "عبور في جذر مساحة الملفات")
    return path


def _open_directory(path, *, create=False):
    """فتح كل مكون دون اتباع رابط؛ لا نعتمد على resolve ثم فتح نصي."""
    fd = os.open(path.anchor, DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(component, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except OSError as exc:
        os.close(fd)
        _io_error(exc)


def _identity(info):
    return {"device": str(info.st_dev), "inode": str(info.st_ino)}


def _private(info, *, directory=False):
    if (info.st_uid != os.getuid() or info.st_mode & 0o077
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or (not directory and info.st_nlink != 1)):
        _fail("unsafe_state", "حالة الأدوات تتطلب ملفًا خاصًا وحيد الرابط")


def _regular(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _fail("unsafe_path", "ملف عادي وحيد الرابط مطلوب")


@contextmanager
def _parent(root_fd, relative):
    parts = relative.split("/")
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd, parts[-1]
    except OSError as exc:
        _io_error(exc)
    finally:
        os.close(fd)


def _read_file(parent_fd, name, limit, *, private=False):
    try:
        _regular(os.stat(name, dir_fd=parent_fd, follow_symlinks=False))
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            _regular(before)
            if private:
                _private(before)
            if before.st_size > limit:
                _fail("file_too_large", "الملف يتجاوز حد البايتات")
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                    before.st_ctime_ns, before.st_nlink) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                    after.st_ctime_ns, after.st_nlink) or len(raw) != before.st_size:
                _fail("file_changed", "تغير الملف أثناء قراءته")
            if len(raw) > limit:
                _fail("file_too_large", "الملف يتجاوز حد البايتات")
            return raw, after
    except OSError as exc:
        _io_error(exc)


def _read_json(fd, name):
    raw, _ = _read_file(fd, name, MAX_STATE_BYTES, private=True)
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                _fail("state_corrupt", "مفتاح JSON مكرر")
            out[key] = value
        return out
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
        canonical_bytes(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("state_corrupt", "حالة الأدوات غير صالحة")


def _write_json(fd, name, value):
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        _private(info)
    temp = "write-" + uuid.uuid4().hex + ".tmp"
    try:
        handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=fd)
        with os.fdopen(handle, "wb") as stream:
            stream.write(canonical_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temp, dir_fd=fd)
        except FileNotFoundError:
            pass


class TextWorkspace:
    def __init__(self, read_root, artifact_root, *, max_bytes=MAX_BYTES):
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES:
            _fail("limit_invalid", "حد النص من بايت واحد إلى 65536")
        self.read_root = None if read_root is None else _root_path(read_root)
        self.artifact_root = _root_path(artifact_root)
        self._read_identity = None
        self.max_bytes = max_bytes
        artifact_fd = _open_directory(self.artifact_root, create=True)
        try:
            self.config = {"schema_version": 1, "max_bytes": max_bytes,
                           "artifact_root": str(self.artifact_root),
                           "artifact_identity": _identity(os.fstat(artifact_fd))}
            fd = self._store(artifact_fd, create=True)
            os.close(fd)
        finally:
            os.close(artifact_fd)
        with self._locked() as (_, fd):
            names = os.listdir(fd)
            if "manifest.json" in names:
                self._load(fd)
            else:
                if any(n != "store.lock" for n in names):
                    _fail("manifest_missing", "حالة سابقة بلا بيان؛ لا إعادة تهيئة")
                _write_json(fd, "manifest.json", self.config)
                self._save(fd, {"config_sha256": digest(self.config), "proposals": []})

    def _store(self, artifact_fd, *, create=False):
        fd = os.dup(artifact_fd)
        try:
            for part in (".diwan-tools", "writes"):
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
                _private(os.fstat(fd), directory=True)
            return fd
        except (OSError, WorkspaceError) as exc:
            os.close(fd)
            if isinstance(exc, OSError):
                _io_error(exc)
            raise

    @contextmanager
    def _locked(self):
        artifact_fd = _open_directory(self.artifact_root)
        state_fd = lock_fd = None
        try:
            if _identity(os.fstat(artifact_fd)) != self.config["artifact_identity"]:
                _fail("root_changed", "جذر المخرجات تغير")
            state_fd = self._store(artifact_fd)
            lock_fd = os.open("store.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                              0o600, dir_fd=state_fd)
            _private(os.fstat(lock_fd))
            try:
                filelock.lock(lock_fd, blocking=False)
            except BlockingIOError:
                _fail("workspace_busy", "عملية أخرى تملك حالة الملفات")
            yield artifact_fd, state_fd
        except OSError as exc:
            _io_error(exc)
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
            if state_fd is not None:
                os.close(state_fd)
            os.close(artifact_fd)

    def _load(self, fd):
        if digest(_read_json(fd, "manifest.json")) != digest(self.config):
            _fail("config_conflict", "جذر الملفات أو إعداداته تغيرت")
        envelope = _read_json(fd, "state.json")
        if not isinstance(envelope, dict) or set(envelope) != {"state", "sha256"}:
            _fail("state_corrupt", "غلاف الحالة غير صالح")
        state = envelope["state"]
        if (not isinstance(state, dict) or set(state) != {"config_sha256", "proposals"}
                or state["config_sha256"] != digest(self.config) or digest(state) != envelope["sha256"]
                or not isinstance(state["proposals"], list) or len(state["proposals"]) > MAX_PROPOSALS):
            _fail("state_corrupt", "بصمة الحالة أو بناؤها غير صالح")
        ids = set()
        for p in state["proposals"]:
            if (not isinstance(p, dict) or set(p) != {"proposal_id", "request_id", "path", "content",
                    "sha256", "size_bytes", "status", "error_code", "owner"}
                    or not isinstance(p["request_id"], str) or not ID.fullmatch(p["request_id"])
                    or p["request_id"] in ids):
                _fail("state_corrupt", "اقتراح غير صالح أو مكرر")
            ids.add(p["request_id"])
            try:
                raw = _utf8(p["content"], self.max_bytes)
                path = _relative(p["path"], writing=True)
            except WorkspaceError:
                _fail("state_corrupt", "محتوى أو مسار اقتراح غير صالح")
            if (p["sha256"] != hashlib.sha256(raw).hexdigest() or type(p["size_bytes"]) is not int
                    or p["size_bytes"] != len(raw)
                    or p["proposal_id"] != self._proposal_id(p["request_id"], path, p["sha256"])
                    or p["status"] not in ("proposed", "pending", "applied", "error")):
                _fail("state_corrupt", "هوية الاقتراح أو حالته غير صالحة")
            owner = p["owner"]
            if owner is not None and (not isinstance(owner, dict) or set(owner) != {"device", "inode"}
                    or any(not isinstance(v, str) or not re.fullmatch(r"[0-9]+", v) for v in owner.values())):
                _fail("state_corrupt", "إيصال ملكية ملف غير صالح")
            if ((p["status"] == "proposed" and owner is not None)
                    or (p["status"] == "applied" and owner is None)
                    or (p["status"] == "error" and p["error_code"] not in ("target_exists", "outcome_uncertain"))
                    or (p["status"] != "error" and p["error_code"] is not None)):
                _fail("state_corrupt", "حالة الإيصال متناقضة")
        return state

    @staticmethod
    def _proposal_id(request_id, path, sha256):
        return "p-" + digest({"request_id": request_id, "path": path, "sha256": sha256})

    @staticmethod
    def _save(fd, state):
        _write_json(fd, "state.json", {"state": state, "sha256": digest(state)})

    @staticmethod
    def _public(proposal, *, replayed=False):
        return {k: copy.deepcopy(v) for k, v in proposal.items() if k != "owner"} | {"replayed": replayed}

    @staticmethod
    def _find(state, proposal_id):
        if not isinstance(proposal_id, str):
            _fail("proposal_unknown", "هوية اقتراح غير معروفة")
        found = next((p for p in state["proposals"] if p["proposal_id"] == proposal_id), None)
        if found is None:
            _fail("proposal_unknown", "هوية اقتراح غير معروفة")
        return found

    def read_text(self, relative_path):
        relative = _relative(relative_path)
        if self.read_root is None:
            _fail("read_root_required", "يجب اختيار جذر القراءة صراحة")
        fd = _open_directory(self.read_root)
        try:
            identity = _identity(os.fstat(fd))
            if self._read_identity is None:
                self._read_identity = identity
            elif identity != self._read_identity:
                _fail("root_changed", "جذر القراءة تغير")
            with _parent(fd, relative) as (parent, name):
                raw, _ = _read_file(parent, name, self.max_bytes)
        finally:
            os.close(fd)
        try:
            content = raw.decode("utf-8", "strict")
        except UnicodeError:
            _fail("text_invalid", "الملف ليس UTF-8 صالحًا")
        _utf8(content, self.max_bytes)
        return FileDocument(relative, hashlib.sha256(raw).hexdigest(), content, len(raw))

    def propose_write(self, relative_filename, content, request_id):
        path = _relative(relative_filename, writing=True)
        raw = _utf8(content, self.max_bytes)
        if not isinstance(request_id, str) or not ID.fullmatch(request_id):
            _fail("request_id_invalid", "هوية طلب الكتابة غير صالحة")
        sha = hashlib.sha256(raw).hexdigest()
        proposal_id = self._proposal_id(request_id, path, sha)
        with self._locked() as (_, fd):
            state = self._load(fd)
            existing = next((p for p in state["proposals"] if p["request_id"] == request_id), None)
            if existing is not None:
                if existing["proposal_id"] != proposal_id:
                    _fail("proposal_conflict", "هوية الطلب نفسها لمحتوى أو هدف آخر")
                return self._public(existing, replayed=True)
            if len(state["proposals"]) >= MAX_PROPOSALS:
                _fail("proposal_limit", "بلغ مخزن الاقتراحات حده")
            p = {"proposal_id": proposal_id, "request_id": request_id, "path": path,
                 "content": content, "sha256": sha, "size_bytes": len(raw),
                 "status": "proposed", "error_code": None, "owner": None}
            state["proposals"].append(p)
            self._save(fd, state)
            return self._public(p)

    def review(self, proposal_id):
        with self._locked() as (_, fd):
            return self._public(self._find(self._load(fd), proposal_id), replayed=True)

    def verified_outputs(self):
        """فهرس قراءة فقط للمخرجات ذات إيصال مكتمل وبايتات مطابقة.

        لا يتبنى ملفًا موجودًا بلا إيصال ولا يستأنف كتابة معلقة. الحالات
        غير المكتملة أو المتغيرة تُعلن منفصلة ولا تُعرض كمرفقات صالحة.
        """
        with self._locked() as (artifact_fd, fd):
            files, unavailable = [], []
            for proposal in self._load(fd)["proposals"]:
                item = {key: proposal[key] for key in ("path", "sha256", "size_bytes")}
                if proposal["status"] == "applied" and self._owned_output(artifact_fd, proposal):
                    files.append(item)
                else:
                    unavailable.append({"path": proposal["path"], "status": proposal["status"],
                        "error_code": "applied_output_changed" if proposal["status"] == "applied"
                        else proposal["error_code"] or "write_not_complete"})
            return {"files": files, "unavailable": unavailable}

    def _owned_output(self, artifact_fd, p):
        if p["owner"] is None:
            return False
        try:
            with _parent(artifact_fd, p["path"]) as (parent, name):
                raw, info = _read_file(parent, name, self.max_bytes, private=True)
            return (_identity(info) == p["owner"] and len(raw) == p["size_bytes"]
                    and hashlib.sha256(raw).hexdigest() == p["sha256"])
        except WorkspaceError:
            return False

    def apply(self, proposal_id, expected_sha256):
        with self._locked() as (artifact_fd, fd):
            state = self._load(fd)
            p = self._find(state, proposal_id)
            if not isinstance(expected_sha256, str) or not SHA.fullmatch(expected_sha256) or expected_sha256 != p["sha256"]:
                _fail("approval_mismatch", "بصمة الموافقة لا تطابق المحتوى المعروض")
            if p["status"] == "applied":
                if not self._owned_output(artifact_fd, p):
                    _fail("applied_output_changed", "الملف المطبق تغير أو اختفى؛ لا إعادة كتابة")
                return self._public(p, replayed=True)
            if p["status"] == "error":
                return self._public(p, replayed=True)
            if p["status"] == "pending":
                p["status"] = "applied" if self._owned_output(artifact_fd, p) else "error"
                p["error_code"] = None if p["status"] == "applied" else "outcome_uncertain"
                self._save(fd, state)
                return self._public(p, replayed=True)
            # Resolve parents before marking pending; absent directories are not created.
            with _parent(artifact_fd, p["path"]) as (parent, name):
                p["status"] = "pending"
                self._save(fd, state)
                try:
                    target_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                        0o600, dir_fd=parent)
                except FileExistsError:
                    p.update(status="error", error_code="target_exists")
                    self._save(fd, state)
                    return self._public(p)
                with os.fdopen(target_fd, "wb") as stream:
                    info = os.fstat(stream.fileno())
                    _private(info)
                    p["owner"] = _identity(info)
                    self._save(fd, state)  # Durable creation receipt before writing content.
                    stream.write(p["content"].encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(parent)
                if not self._owned_output(artifact_fd, p):
                    p.update(status="error", error_code="outcome_uncertain")
                else:
                    p["status"] = "applied"
                self._save(fd, state)
                return self._public(p)
