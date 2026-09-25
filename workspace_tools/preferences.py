"""تفضيلات محلية صريحة؛ لا استنتاج من المحادثة ولا تخزين أسرار.

الملف غير مشفر، ومحمي بأذونات حساب المالك. البصمة تكشف فساد الحالة
ولا تمنع مالك القرص من استبدال الحالة والبصمة أو استعادة نسخة قديمة.
كل تعديل يتطلب نسخة متوقعة تحت قفل واحد لمنع ضياع تحديث متزامن.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import stat
import unicodedata
import uuid

from core import filelock
from core.canonical import SAFE_INT, canonical_bytes, digest

_KEYS = frozenset({"response_language", "verbosity", "address_name"})
_CHOICES = {"response_language": frozenset({"ar", "en"}),
            "verbosity": frozenset({"concise", "balanced", "detailed"})}
_MAX_STATE_BYTES = 8192


class PreferenceError(ValueError):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{reason} [{code}]")
        self.code, self.reason = code, reason


def _fail(code: str, reason: str):
    raise PreferenceError(code, reason)


def _key(key):
    if type(key) is not str or key not in _KEYS:
        _fail("preference_key_invalid", "مفتاح تفضيل غير معتمد")


def _value(key, value):
    if type(value) is not str:
        _fail("preference_value_invalid", "قيمة التفضيل نص صريح")
    if key in _CHOICES:
        if value not in _CHOICES[key]:
            _fail("preference_value_invalid", "قيمة تفضيل خارج الاختيارات المعتمدة")
        return
    if (not 1 <= len(value) <= 80 or not value.strip()
            or any(unicodedata.category(ch) in {"Cc", "Cf", "Cs"} for ch in value)):
        _fail("preference_value_invalid", "اسم المخاطبة قصير وغير فارغ ودون محارف تحكم")


def _revision(revision):
    if type(revision) is not int or not 0 <= revision <= SAFE_INT:
        _fail("preference_revision_invalid", "نسخة متوقعة صحيحة وغير سالبة مطلوبة")


def validate_snapshot(value) -> dict:
    """يتحقق لقطة واردة دون ملفات، ويعيد نسخة منفصلة أو رفضًا مسمى."""
    try:
        if type(value) is not dict or set(value) != {"revision", "values", "sha256"}:
            _fail("preference_snapshot_invalid", "حقول لقطة التفضيلات غير صالحة")
        _revision(value["revision"])
        if type(value["values"]) is not dict:
            _fail("preference_snapshot_invalid", "قيم اللقطة ليست كائنًا")
        if value["revision"] == 0 and value["values"]:
            _fail("preference_snapshot_invalid", "نسخة البداية يجب أن تكون فارغة")
        for key, item in value["values"].items():
            _key(key)
            _value(key, item)
        body = {"revision": value["revision"], "values": dict(value["values"])}
        if type(value["sha256"]) is not str or digest(body) != value["sha256"]:
            _fail("preference_snapshot_invalid", "بصمة لقطة التفضيلات غير مطابقة")
        return {**body, "sha256": value["sha256"]}
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, PreferenceError) and exc.code == "preference_snapshot_invalid":
            raise
        raise PreferenceError("preference_snapshot_invalid", "لقطة التفضيلات غير صالحة") from exc


def _check_stat(info, *, directory=False):
    expected_mode = 0o700 if directory else 0o600
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(info.st_mode) or (not directory and info.st_nlink != 1):
        _fail("preference_unsafe_path", "مسار غير عادي أو متعدد الروابط")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != expected_mode:
        _fail("preference_unsafe_permissions", "التفضيلات تتطلب أذونات خاصة بمالكها")


def _entry(directory_fd, name, *, missing=False):
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing:
            return False
        _fail("preference_state_missing", "جزء من مخزن التفضيلات غائب؛ لا إعادة إنشاء")
    _check_stat(info)
    return True


def _open_root(path, *, create=False):
    """مقبض ثابت دون اتباع أي مكوّن رابط، حتى عند تبديل المسار لاحقًا."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        _check_stat(os.fstat(fd), directory=True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _io_error(exc):
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        _fail("preference_unsafe_path", "رابط رمزي أو مسار غير عادي")
    if exc.errno == errno.ENOENT:
        _fail("preference_state_missing", "جزء من مخزن التفضيلات غائب؛ لا إعادة إنشاء")
    raise PreferenceError("preference_filesystem_error", "تعذر فتح أو حفظ التفضيلات") from exc


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail("preference_state_corrupt", "مفتاح JSON مكرر")
        result[key] = value
    return result


class Preferences:
    """root هو دليل التفضيلات نفسه، مثل var/preferences، لا جذر المشروع.

    snapshot.sha256 يبصم {revision, values}. غلاف الملف يبصم أيضًا
    schema_version؛ snapshot نتيجة منفصلة لا مرجع قابل لتعديل المخزن.
    """

    def __init__(self, root: Path):
        try:
            raw = Path(root).expanduser()
            if ".." in raw.parts:
                _fail("preference_unsafe_path", "صعود المسار غير مسموح")
            self.root = raw.absolute()
            self.state_path = self.root / "state.json"
            self.lock_path = self.root / "preferences.lock"
            root_fd = _open_root(self.root, create=True)
            try:
                info = os.fstat(root_fd)
                self._root_identity = (info.st_dev, info.st_ino)
            finally:
                os.close(root_fd)
            with self._lock(initialize=True) as (root_fd, new_lock):
                if _entry(root_fd, self.state_path.name, missing=True):
                    self._load(root_fd)
                else:
                    # قفل سابق أو أي أثر آخر يمنع تحويل حذف/انقطاع إلى
                    # مخزن جديد صامت. انقطاع أول إنشاء يغلق المسار أيضًا.
                    if not new_lock or any(name != self.lock_path.name for name in os.listdir(root_fd)):
                        _fail("preference_state_missing", "حالة غائبة عن مخزن سابق؛ لا إعادة تهيئة")
                    self._write(root_fd, {"schema_version": 1, "revision": 0, "values": {}})
        except OSError as exc:
            _io_error(exc)

    @contextmanager
    def _lock(self, *, initialize=False):
        fd = root_fd = None
        try:
            root_fd = _open_root(self.root)
            info = os.fstat(root_fd)
            if (info.st_dev, info.st_ino) != self._root_identity:
                _fail("preference_root_changed", "تغير جذر مخزن التفضيلات")
            exists = _entry(root_fd, self.lock_path.name, missing=initialize)
            new_lock = False
            if not exists:
                if os.listdir(root_fd):
                    _fail("preference_state_missing", "آثار مخزن بلا قفل؛ لا إعادة تهيئة")
                try:
                    fd = os.open(self.lock_path.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=root_fd)
                    new_lock = True
                except FileExistsError:
                    _entry(root_fd, self.lock_path.name)
            if fd is None:
                fd = os.open(self.lock_path.name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
            _check_stat(os.fstat(fd))
            try:
                filelock.lock(fd, blocking=False)
            except BlockingIOError:
                _fail("preference_busy", "مخزن التفضيلات قيد الاستخدام")
            try:
                yield root_fd, new_lock
            finally:
                filelock.unlock(fd)
        except OSError as exc:
            _io_error(exc)
        finally:
            if fd is not None:
                os.close(fd)
            if root_fd is not None:
                os.close(root_fd)

    def _load(self, root_fd):
        _entry(root_fd, self.state_path.name)
        fd = os.open(self.state_path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            _check_stat(info)
            if info.st_size > _MAX_STATE_BYTES:
                _fail("preference_state_corrupt", "حالة التفضيلات أكبر من العقد")
            raw = stream.read(_MAX_STATE_BYTES + 1)
        if len(raw) > _MAX_STATE_BYTES:
            _fail("preference_state_corrupt", "حالة التفضيلات أكبر من العقد")
        try:
            envelope = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_pairs)
            if type(envelope) is not dict or set(envelope) != {"state", "sha256"}:
                _fail("preference_state_corrupt", "غلاف التفضيلات غير صالح")
            state = envelope["state"]
            if (type(state) is not dict or set(state) != {"schema_version", "revision", "values"}
                    or type(state["schema_version"]) is not int or state["schema_version"] != 1
                    or type(state["values"]) is not dict
                    or digest(state) != envelope["sha256"]):
                _fail("preference_state_corrupt", "عقد التفضيلات أو بصمته غير صالح")
            _revision(state["revision"])
            if state["revision"] == 0 and state["values"]:
                _fail("preference_state_corrupt", "نسخة البداية يجب أن تكون فارغة")
            for key, value in state["values"].items():
                _key(key)
                _value(key, value)
            return state
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            if isinstance(exc, PreferenceError) and exc.code == "preference_state_corrupt":
                raise
            raise PreferenceError("preference_state_corrupt", "محتوى التفضيلات غير صالح") from exc

    def _write(self, root_fd, state):
        _check_stat(os.fstat(root_fd), directory=True)
        _entry(root_fd, self.state_path.name, missing=True)
        temp = "write-" + uuid.uuid4().hex + ".tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                _check_stat(os.fstat(stream.fileno()))
                stream.write(canonical_bytes({"state": state, "sha256": digest(state)}))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.state_path.name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
            os.fsync(root_fd)
        finally:
            try:
                os.unlink(temp, dir_fd=root_fd)
            except FileNotFoundError:
                pass

    @staticmethod
    def _snapshot(state):
        result = {"revision": state["revision"], "values": dict(state["values"])}
        return {**result, "sha256": digest(result)}

    def snapshot(self) -> dict:
        with self._lock() as (root_fd, _):
            return self._snapshot(self._load(root_fd))

    def _change(self, key, value, expected_revision, *, delete):
        _key(key)
        _revision(expected_revision)
        if not delete:
            _value(key, value)
        with self._lock() as (root_fd, _):
            state = self._load(root_fd)
            if state["revision"] != expected_revision:
                _fail("preference_revision_conflict", "نسخة التفضيلات تغيرت؛ اقرأها قبل تعديل جديد")
            if delete and key not in state["values"]:
                _fail("preference_missing", "التفضيل المطلوب حذفه غير موجود")
            if state["revision"] >= SAFE_INT:
                _fail("preference_revision_limit", "بلغ عداد التفضيلات الحد المسموح")
            state["revision"] += 1
            if delete:
                del state["values"][key]
            else:
                state["values"][key] = value
            self._write(root_fd, state)
            return self._snapshot(state)

    def set(self, key: str, value: str, expected_revision: int) -> dict:
        return self._change(key, value, expected_revision, delete=False)

    def delete(self, key: str, expected_revision: int) -> dict:
        return self._change(key, None, expected_revision, delete=True)
