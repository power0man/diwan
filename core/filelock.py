"""قفلٌ حصريٌّ واحد لكل المنصّات (ج٧، #37).

`fcntl` لا يوجد على ويندوز، وكانت ثلاثَ عشرةَ وحدةً من ديوان تستورده، فينهار
الاستيرادُ هناك قبل أن يعمل شيء (`docs/SECOND-NODE.md`). فصار القفلُ من هنا وحده:
- على POSIX: `flock`، كما كان.
- على ويندوز: `msvcrt.locking` لبايتٍ واحدٍ بعيدٍ عن البيانات. وقفلُ ويندوز إلزاميّ،
  فلو أُقفل البايتُ الأول لحُجبت قراءةُ الملف نفسِه عن غير مالك القفل.

الانشغالُ يُرفع `BlockingIOError` على المنصّتين، كما كان `LOCK_NB` يرفعه، فلا
يتغيّر شيءٌ عند من يلتقطه.
"""
from __future__ import annotations

import errno
import os
import time

try:
    import fcntl
except ImportError:  # ويندوز
    fcntl = None
    import msvcrt
else:
    msvcrt = None

# بايتُ القفل على ويندوز: بعيدٌ عن كل ما يكتبه ديوان، وداخل مدى الإزاحة
# الموقَّعة بـ٣٢ بتًّا التي تقبلها `_locking`.
WINDOWS_LOCK_OFFSET = 0x7FFFFFF0
# ما يردّ به `msvcrt.locking` حين يملك القفلَ غيرُه؛ وما سواه عطبٌ لا انتظار.
_WINDOWS_BUSY = frozenset({errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK)})
_RETRY_S = 0.05


def _fd(target) -> int:
    return target if isinstance(target, int) else target.fileno()


def _windows_range(fd: int, mode: int) -> None:
    """يقفل البايتَ البعيد أو يفكّه، ويعيد موضعَ الواصف كما كان.

    `msvcrt.locking` يبدأ من موضع الواصف الحالي، وقد يكون الواصفُ تحت مجرًى
    مخزَّن يقرأ منه صاحبُه؛ فالموضعُ يُحفظ ويُعاد في كل حال.
    """
    position = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, WINDOWS_LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, mode, 1)
    finally:
        os.lseek(fd, position, os.SEEK_SET)


def lock(target, *, blocking: bool = True) -> None:
    """قفلٌ حصريّ على واصفٍ أو مجرًى. بلا انتظار يُرفع `BlockingIOError` إن انشغل."""
    fd = _fd(target)
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    while True:
        try:
            _windows_range(fd, msvcrt.LK_NBLCK)
            return
        except OSError as exc:
            if exc.errno not in _WINDOWS_BUSY:
                raise
            if not blocking:
                raise BlockingIOError(exc.errno, "القفلُ تملكه عمليةٌ أخرى") from None
        time.sleep(_RETRY_S)


def unlock(target) -> None:
    """يفكّ قفلًا أخذه `lock` على الواصف نفسِه."""
    fd = _fd(target)
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    _windows_range(fd, msvcrt.LK_UNLCK)
