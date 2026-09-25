"""دفترُ الرجوع: ما يُردّ لا يحتاج إذنًا واقعةً بواقعة.

هذا الملفُّ هو المفتاحُ العمليُّ لـ«العمل دون تدخّل». والقاعدةُ فيه واحدة:

    **تُودَع الحالةُ السابقة قبل أن يقع الأثر، لا بعده.**

فانقطاعٌ بين الإيداع والكتابة يترك أثرًا لم يقع ونسخةً محفوظة — والرجوعُ
يستعيد المحتوى نفسه فلا يضرّ. والعكسُ (كتابةٌ ثم إيداع) يترك أثرًا واقعًا
بلا نسخة، فلا رجوع. الترتيبُ هو الضمانة، لا النيّة.

ويُرفض الرجوعُ إن تغيّر الملفُّ بعد الفعل (`changed_since_action`): الرجوعُ
يردّ فِعلَ الوكيل، ولا يمحو تعديلًا لاحقًا لم يُودِع أحدٌ نسخته.

والدفترُ داخل مجلّدٍ مخفيّ، و`_relative(writing=True)` يرفض المسارات
المخفيّة — فلا تستطيع أداةٌ أن تكتب فوق دفترِ رجوعها.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

from workspace_tools.files import _relative

JOURNAL_DIR = ".diwan-journal"
MAX_BYTES = 262_144
MAX_JOURNAL_BYTES = 64 * 1024 * 1024
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_SHA = re.compile(r"[a-f0-9]{64}\Z")


class JournalRefused(RuntimeError):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{reason} [{code}]")
        self.code = code
        self.reason = reason


def _fail(code: str, reason: str):
    raise JournalRefused(code, reason)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _identity(info):
    return info.st_dev, info.st_ino


def _stamp(info):
    return (*_identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _exact(parent, name):
    if name in os.listdir(parent):
        return True
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    _fail("path_alias", "تهجئة بديلة لمسار قائم")


def _directory(parent, name, *, create=False):
    if not _exact(parent, name):
        if not create:
            raise FileNotFoundError(name)
        os.mkdir(name, 0o700, dir_fd=parent)
        os.fsync(parent)
    return os.open(name, _DIRECTORY, dir_fd=parent)


def _open_directory(path, *, create=False):
    fd = os.open(path.anchor, _DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = _directory(fd, part, create=create)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _regular(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
        _fail("unsafe_path", "ملف عادي مستقل يملكه المستخدم مطلوب")


def _read(parent, name, limit):
    """One no-follow descriptor, bounded bytes and stable identity; None means absent."""
    try:
        if not _exact(parent, name):
            return None
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            _regular(before)
            if before.st_size > limit:
                _fail("file_too_large", "الملف يتجاوز حد القراءة الآمنة")
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if after.st_nlink == 0:
                # استُبدل الملفُّ ذرّيًّا (os.replace من كاتبٍ مصرَّحٍ له) بعد فتحه: تغيُّرٌ يُعاد بعده القراءة،
                # لا مسارٌ غيرُ آمن — سباقُ إيقافين كشفه CI في ٢٥ سبتمبر ٢٠٢٦ (ك٢٨)
                _fail("file_changed", "استُبدل الملف أثناء قراءته")
            _regular(after)
            entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (_stamp(before) != _stamp(after) or _identity(entry) != _identity(after)
                    or len(raw) != before.st_size):
                _fail("file_changed", "تغيّر الملف أثناء قراءته")
            return raw, _stamp(after)
    except OSError:
        _fail("unsafe_path", "تعذرت قراءة ملف مستقل دون اتباع روابط")


@dataclass(frozen=True)
class Action:
    action_id: str
    kind: str
    path: str
    before_sha256: str | None   # None يعني: لم يكن الملفُّ موجودًا
    after_sha256: str
    at: str


class Journal:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        if ".." in self.root.parts:
            _fail("path_invalid", "عبور في جذر دفتر الرجوع")
        self._root_path = self.root
        self._root_identity = None
        self.dir = self.root / JOURNAL_DIR
        self.path = self.dir / "journal.jsonl"
        self.blobs = self.dir / "blobs"
        self._mutex = threading.RLock()
        self._active = None
        self._target_state = None
        try:
            fd = _open_directory(self.root)
        except FileNotFoundError:
            pass  # Construction does not create a missing workspace.
        except OSError:
            _fail("unsafe_path", "جذر دفتر الرجوع يحتوي رابطًا أو مكونًا غير آمن")
        else:
            self._root_identity = _identity(os.fstat(fd))
            os.close(fd)

    def _check(self):
        root, store, lock = self._active
        if (self.root != self._root_path or self.dir != self.root / JOURNAL_DIR
                or self.path != self.dir / "journal.jsonl" or self.blobs != self.dir / "blobs"):
            _fail("workspace_changed", "تغيّر جذر دفتر الرجوع")
        try:
            current = _open_directory(self._root_path)
            try:
                if _identity(os.fstat(current)) != self._root_identity:
                    _fail("workspace_changed", "استُبدل جذر دفتر الرجوع")
            finally:
                os.close(current)
            if _identity(os.stat(JOURNAL_DIR, dir_fd=root, follow_symlinks=False)) != _identity(os.fstat(store)):
                _fail("journal_changed", "استُبدل دليل دفتر الرجوع")
            if lock is not None:
                info = os.stat("journal.lock", dir_fd=store, follow_symlinks=False)
                _regular(info)
                if _identity(info) != _identity(os.fstat(lock)):
                    _fail("journal_changed", "استُبدل قفل دفتر الرجوع")
        except OSError:
            _fail("unsafe_path", "تغيّر مسار دفتر الرجوع")

    @contextmanager
    def _operation(self, *, create=False, mutate=False):
        """Serialize cooperating writers; descriptors never re-resolve a symlink.

        A host-owner process ignoring this lock can still modify a file after
        the final comparison and before rename/unlink: POSIX offers no atomic
        compare-and-swap by content. This is not isolation from a hostile owner.
        """
        with self._mutex:
            if self._active is not None:
                self._check()
                yield self._active
                return
            root = store = lock = None
            try:
                try:
                    if self.root != self._root_path:
                        _fail("workspace_changed", "تغيّر جذر دفتر الرجوع")
                    root = _open_directory(self.root, create=create)
                    identity = _identity(os.fstat(root))
                    if self._root_identity is None:
                        self._root_identity = identity
                    elif identity != self._root_identity:
                        _fail("workspace_changed", "استُبدل جذر دفتر الرجوع")
                    store = _directory(root, JOURNAL_DIR, create=create)
                    info = os.fstat(store)
                    if info.st_uid != os.getuid() or info.st_mode & 0o022:
                        _fail("unsafe_path", "دليل الرجوع لا يقبل كتابة الآخرين")
                    lock_exists = _exact(store, "journal.lock")
                    if create or mutate or lock_exists:
                        lock = os.open("journal.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
                                       | (os.O_CREAT if create or mutate else 0), 0o600, dir_fd=store)
                        _regular(os.fstat(lock))
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            _fail("journal_busy", "دفتر الرجوع قيد عملية أخرى")
                except FileNotFoundError:
                    if create or (root is None and self._root_identity is not None):
                        _fail("workspace_changed", "مسار دفتر الرجوع غائب")
                    yield None
                    return
                except OSError:
                    _fail("unsafe_path", "مسار دفتر الرجوع ليس دليلًا مستقلًا آمنًا")
                self._active = (root, store, lock)
                self._check()
                yield self._active
            finally:
                self._active = None
                self._target_state = None
                for fd in (lock, store, root):
                    if fd is not None:
                        os.close(fd)

    def _parent(self, relative, *, create=False):
        root = self._active[0]
        fd = os.dup(root)
        try:
            for part in relative.split("/")[:-1]:
                child = _directory(fd, part, create=create)
                os.close(fd)
                fd = child
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _check_parent(self, fd, relative):
        self._check()
        try:
            current = self._parent(relative)
            try:
                if _identity(os.fstat(current)) != _identity(os.fstat(fd)):
                    _fail("path_changed", "استُبدل دليل الملف")
            finally:
                os.close(current)
        except OSError:
            _fail("unsafe_path", "تغيّر دليل الملف أو أصبح رابطًا")

    def _replace(self, parent, name, raw, expected, *, check, limit=MAX_BYTES):
        temp = ".journal-write-" + uuid.uuid4().hex
        try:
            check()
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            check()
            if _read(parent, name, limit) != expected:
                _fail("changed_since_action", "تغيّر الملف قبل تثبيت العملية؛ حُفظ تعديل المالك")
            os.replace(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temp, dir_fd=parent)
            except FileNotFoundError:
                pass

    # — القراءة —

    def actions(self) -> list[Action]:
        with self._operation() as state:
            if state is None:
                return []
            snapshot = _read(state[1], "journal.jsonl", MAX_JOURNAL_BYTES)
            return self._decode_actions(snapshot[0]) if snapshot is not None else []

    @staticmethod
    def _decode_actions(raw):
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate")
                result[key] = value
            return result
        try:
            out, ids = [], set()
            for line in raw.decode("utf-8").splitlines():
                if not line.strip():
                    continue
                action = Action(**json.loads(line, object_pairs_hook=pairs))
                if (not isinstance(action.action_id, str) or not re.fullmatch(r"act-[a-f0-9]{16}", action.action_id)
                        or action.action_id in ids or action.kind != "write_file"
                        or not isinstance(action.after_sha256, str) or not _SHA.fullmatch(action.after_sha256)
                        or (action.before_sha256 is not None and (not isinstance(action.before_sha256, str)
                            or not _SHA.fullmatch(action.before_sha256)))
                        or not isinstance(action.at, str) or not action.at):
                    raise ValueError("invalid action")
                _relative(action.path, writing=True)
                ids.add(action.action_id)
                out.append(action)
            return out
        except (ValueError, TypeError, UnicodeError, RecursionError):
            _fail("journal_corrupt", "سجل الرجوع غير صالح")

    def action(self, action_id: str) -> Action:
        for entry in self.actions():
            if entry.action_id == action_id:
                return entry
        _fail("action_unknown", f"لا فعلَ بهذا المعرّف: {action_id!r}")

    # — الكتابة الرَّجعية —

    def _target(self, relative: str) -> Path:
        relative = _relative(relative, writing=True)
        return self.root / relative

    def _keep(self, raw: bytes) -> str:
        sha = _sha(raw)
        fd = self._blob_directory(create=True)
        try:
            snapshot = _read(fd, sha, MAX_BYTES)
            if snapshot is not None and _sha(snapshot[0]) != sha:
                _fail("blob_corrupt", "نسخة الرجوع القائمة لا تطابق بصمتها")
            if snapshot is None:
                self._replace(fd, sha, raw, None, check=lambda: self._check_blob_directory(fd))
        finally:
            os.close(fd)
        return sha

    def _blob_directory(self, *, create=False):
        self._check()
        try:
            return _directory(self._active[1], "blobs", create=create)
        except FileNotFoundError:
            _fail("blob_missing", "نسخة ما قبل الفعل مفقودة")
        except OSError:
            _fail("unsafe_path", "دليل نسخ الرجوع ليس مستقلًا آمنًا")

    def _check_blob_directory(self, fd):
        self._check()
        try:
            info = os.stat("blobs", dir_fd=self._active[1], follow_symlinks=False)
        except OSError:
            _fail("journal_changed", "دليل نسخ الرجوع غائب أو متغير")
        if _identity(info) != _identity(os.fstat(fd)):
            _fail("journal_changed", "استُبدل دليل نسخ الرجوع")

    def _append(self, action: Action) -> None:
        store = self._active[1]
        snapshot = _read(store, "journal.jsonl", MAX_JOURNAL_BYTES)
        raw = b"" if snapshot is None else snapshot[0]
        self._decode_actions(raw)
        addition = (json.dumps(action.__dict__, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        if raw and not raw.endswith(b"\n"):
            _fail("journal_corrupt", "آخر قيد في الدفتر غير مكتمل")
        if len(raw) + len(addition) > MAX_JOURNAL_BYTES:
            _fail("file_too_large", "دفتر الرجوع بلغ حد الحفظ")
        self._replace(store, "journal.jsonl", raw + addition, snapshot,
                      check=self._check, limit=MAX_JOURNAL_BYTES)

    def _place(self, target: Path, raw: bytes) -> None:
        expected_target, parent, relative, snapshot = self._target_state
        if target != expected_target:
            _fail("workspace_changed", "هدف الكتابة لا يطابق العملية المثبتة")
        self._replace(parent, target.name, raw, snapshot,
                      check=lambda: self._check_parent(parent, relative))

    def write_file(self, relative: str, content: str) -> Action:
        """يودِع ثم يكتب. الترتيبُ عقدٌ لا تفصيل."""
        if not isinstance(content, str):
            _fail("content_invalid", "محتوى نصّيّ مطلوب")
        raw = content.encode("utf-8", "strict")
        if len(raw) > MAX_BYTES:
            _fail("file_too_large", f"فوق {MAX_BYTES} بايت")
        target = self._target(relative)
        relative = _relative(relative, writing=True)
        with self._operation(create=True):
            self.actions()  # Refuse unsafe/corrupt history before changing any target.
            try:
                parent = self._parent(relative, create=True)
            except OSError:
                _fail("unsafe_path", "دليل الهدف غير آمن")
            try:
                snapshot = _read(parent, target.name, MAX_BYTES)
                before_sha = None if snapshot is None else self._keep(snapshot[0])
                action = Action(action_id="act-" + uuid.uuid4().hex[:16], kind="write_file",
                                path=relative, before_sha256=before_sha, after_sha256=_sha(raw),
                                at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                self._append(action)
                self._target_state = (target, parent, relative, snapshot)
                self._place(target, raw)
                return action
            finally:
                os.close(parent)

    def revert(self, action_id: str) -> dict:
        with self._operation(mutate=True) as state:
            if state is None:
                _fail("action_unknown", "فعل الرجوع غير موجود")
            action = self.action(action_id)
            target = self._target(action.path)
            try:
                parent = self._parent(action.path)
            except FileNotFoundError:
                if action.before_sha256 is None:
                    return {"status": "already_reverted", "action_id": action_id, "result": "no_change"}
                _fail("changed_since_action", "الملف السابق أو دليله مفقود")
            except OSError:
                _fail("unsafe_path", "دليل الهدف غير آمن")
            try:
                return self._revert(action, target, parent)
            finally:
                os.close(parent)

    def _revert(self, action, target, parent):
        action_id = action.action_id
        snapshot = _read(parent, target.name, MAX_BYTES)
        current_sha = None if snapshot is None else _sha(snapshot[0])
        if current_sha != action.after_sha256:
            # الحالُ كما كانت قبل الفعل: إمّا لم يقع (انقطاعٌ بين الإيداع
            # والكتابة) وإمّا رُدَّ سلفًا. فالرجوعُ عدمُ عملٍ **مُعلَن** لا
            # رفض — وإلّا صار الانقطاعُ يحبس الحالةَ بلا سبيلٍ لتأكيدها.
            if current_sha == action.before_sha256:
                return {"status": "already_reverted", "action_id": action_id,
                        "result": "no_change"}
            _fail("changed_since_action",
                  "تغيّر الملفُّ بعد الفعل؛ الرجوعُ يردّ فعلَ الوكيل لا يمحو غيره")
        if action.before_sha256 is None:
            self._check_parent(parent, action.path)
            if _read(parent, target.name, MAX_BYTES) != snapshot:
                _fail("changed_since_action", "تغيّر الملف قبل الرجوع")
            os.unlink(target.name, dir_fd=parent)
            os.fsync(parent)
            return {"status": "reverted", "action_id": action_id, "result": "removed"}
        fd = self._blob_directory()
        try:
            blob = _read(fd, action.before_sha256, MAX_BYTES)
            if blob is None:
                _fail("blob_missing", "نسخة ما قبل الفعل مفقودة")
            if _sha(blob[0]) != action.before_sha256:
                _fail("blob_corrupt", "نسخة الرجوع لا تطابق بصمة اسمها")
            self._check_blob_directory(fd)
        finally:
            os.close(fd)
        self._target_state = (target, parent, action.path, snapshot)
        self._place(target, blob[0])
        return {"status": "reverted", "action_id": action_id, "result": "restored"}
