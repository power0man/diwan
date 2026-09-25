"""القفلُ من `core/filelock.py` وحده (ج٧، #37).

ثلاثة أشياء تُحرس هنا:
١. لا وحدةَ في ديوان تستورد `fcntl` غير `core/filelock.py`، فلا ينهار الاستيرادُ على
   ويندوز. والاستثناءُ الوحيد مسمًّى بسببه.
٢. على POSIX: القفلُ الحصريّ يُرفض بلا انتظار بـ`BlockingIOError` ما دام غيرُه يملكه،
   ويُؤخذ بعد فكّه، بواصفٍ أو بمجرًى.
٣. مسارُ ويندوز بمحاكاةٍ لـ`msvcrt`: البايتُ البعيد لا الأول، وموضعُ الواصف يُعاد،
   والانشغالُ `BlockingIOError`، والانتظارُ يعيد المحاولة للانشغال وحده.

الحد: مسارُ ويندوز هنا محاكًى؛ وتشغيلُه الحقيقيّ على Nitro (#21).
"""
from __future__ import annotations

import ast
import errno
import os
import subprocess
import threading
from pathlib import Path

import pytest

from core import filelock

ROOT = Path(__file__).resolve().parent.parent
# المشرفُ على المشغّل يُثبَّت سكربتًا مستقلًّا على مضيف الماك خارج المستودع، فلا
# يصل إلى `core`؛ ويعتمد على `os.getuid` والإشارات، فهو POSIX بطبعه.
ALLOWED = {"core/filelock.py", "ci/run_persistent_runner.py"}


def _tracked_python() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    return [p for p in out if not p.startswith("tests/")]


def _imports_fcntl(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "fcntl" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "fcntl":
            return True
    return False


def test_no_module_outside_filelock_imports_fcntl():
    offenders = [p for p in _tracked_python()
                 if p not in ALLOWED and _imports_fcntl(ROOT / p)]
    assert offenders == [], f"يستورد fcntl مباشرةً: {offenders}"


def test_the_named_exception_still_needs_its_exemption():
    """الاستثناءُ لا يبقى بعد زوال سببه: إن كفّ عن الاستيراد يُحذف من القائمة."""
    assert _imports_fcntl(ROOT / "ci/run_persistent_runner.py")


@pytest.mark.skipif(filelock.fcntl is None, reason="POSIX وحدها")
@pytest.mark.parametrize("as_stream", [False, True])
def test_a_held_lock_refuses_without_waiting_then_yields_after_unlock(tmp_path, as_stream):
    path = tmp_path / "x.lock"
    path.write_bytes(b"")
    first = os.open(path, os.O_RDWR)
    second = open(path, "r+b") if as_stream else os.open(path, os.O_RDWR)
    try:
        filelock.lock(first, blocking=False)
        with pytest.raises(BlockingIOError):
            filelock.lock(second, blocking=False)
        filelock.unlock(first)
        filelock.lock(second, blocking=False)
        filelock.unlock(second)
    finally:
        os.close(first)
        second.close() if as_stream else os.close(second)


@pytest.mark.skipif(filelock.fcntl is None, reason="POSIX وحدها")
def test_the_blocking_lock_waits_for_the_holder(tmp_path):
    path = tmp_path / "x.lock"
    path.write_bytes(b"")
    first, second = os.open(path, os.O_RDWR), os.open(path, os.O_RDWR)
    got = threading.Event()
    try:
        filelock.lock(first)
        waiter = threading.Thread(target=lambda: (filelock.lock(second), got.set()))
        waiter.start()
        assert not got.wait(0.3), "أخذ القفلَ وغيرُه يملكه"
        filelock.unlock(first)
        assert got.wait(5), "لم يأخذ القفلَ بعد فكّه"
        waiter.join(5)
    finally:
        os.close(first)
        os.close(second)


# — مسارُ ويندوز بمحاكاة msvcrt —

class FakeMsvcrt:
    LK_NBLCK, LK_UNLCK = 2, 0

    def __init__(self, fail=()):
        self.calls: list[tuple[int, int, int]] = []
        self.fail = list(fail)

    def locking(self, fd, mode, nbytes):
        self.calls.append((os.lseek(fd, 0, os.SEEK_CUR), mode, nbytes))
        if mode == self.LK_NBLCK and self.fail:
            code = self.fail.pop(0)
            raise OSError(code, os.strerror(code))


@pytest.fixture
def windows(monkeypatch):
    def install(fail=()):
        fake = FakeMsvcrt(fail)
        monkeypatch.setattr(filelock, "fcntl", None)
        monkeypatch.setattr(filelock, "msvcrt", fake, raising=False)
        return fake
    return install


@pytest.fixture
def data_fd(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_bytes(b"0123456789")
    fd = os.open(path, os.O_RDWR)
    os.lseek(fd, 4, os.SEEK_SET)
    yield fd
    os.close(fd)


def test_windows_locks_one_far_byte_and_restores_the_position(windows, data_fd):
    fake = windows()
    filelock.lock(data_fd, blocking=False)
    filelock.unlock(data_fd)
    assert fake.calls == [(filelock.WINDOWS_LOCK_OFFSET, fake.LK_NBLCK, 1),
                          (filelock.WINDOWS_LOCK_OFFSET, fake.LK_UNLCK, 1)]
    assert os.lseek(data_fd, 0, os.SEEK_CUR) == 4
    assert filelock.WINDOWS_LOCK_OFFSET > 10 ** 9


def test_windows_busy_without_waiting_is_blocking_io_error(windows, data_fd):
    windows(fail=[errno.EACCES])
    with pytest.raises(BlockingIOError):
        filelock.lock(data_fd, blocking=False)
    assert os.lseek(data_fd, 0, os.SEEK_CUR) == 4


def test_windows_waiting_retries_only_while_busy(windows, data_fd, monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(filelock.time, "sleep", slept.append)
    fake = windows(fail=[errno.EACCES, errno.EACCES])
    filelock.lock(data_fd)
    assert len(fake.calls) == 3 and len(slept) == 2


def test_windows_a_fault_that_is_not_busy_is_raised_not_waited_on(windows, data_fd, monkeypatch):
    def no_sleep(_s):
        raise AssertionError("انتظر عطبًا لا انشغالًا")
    monkeypatch.setattr(filelock.time, "sleep", no_sleep)
    windows(fail=[errno.EBADF])
    with pytest.raises(OSError) as err:
        filelock.lock(data_fd)
    assert err.value.errno == errno.EBADF
    assert not isinstance(err.value, BlockingIOError)
