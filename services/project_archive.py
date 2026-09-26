"""مشروعُ المستخدم في مساحة المشروع (ج٩، الشطر الثاني): استيرادُ مشروعٍ مضغوط، وتصديرُ مجلّدٍ مضغوطًا.

**الاستيرادُ كلُّه أو لا شيء.** يُقرأ الأرشيفُ ويُفحص كلُّه قبل أن يُكتب ملفٌّ واحد:
- **المسارات:** نسبيّة، بلا عبور، ولا مطلقة، ولا شرطةٍ خلفية، ولا روابط رمزية.
  وما خالفها يردّ الأرشيفَ كلَّه بـ`archive_path_unsafe`.
- **الحدود:**
  - عددُ الملفّات، وحجمُ كلٍّ منها (حدُّ دفتر الرجوع)، والمجموع.
  - والحجمُ المعلَن يُفحص قبل الفكّ، والفكُّ لا يتجاوزه، وما يكذب في حجمه يُردّ ببصمته (`archive_corrupt`).
- **ما يُتخطّى ويُسمّى:** المخفيّ (`.git/` و`.env`…)، ومخلّفاتُ الأنظمة (`__MACOSX/` و`__pycache__/` و`node_modules/`).
  فالمساحةُ لا تقبل مسارًا مخفيًّا، والأسرارُ لا تُنسخ إليها بلا قصد.
- **الوجهة:** مجلّدٌ جديد باسمٍ يختاره المالك، ولا يُكتب فوق مجلّدٍ قائم.
  وإن جمع الأرشيفُ ملفّاتِه تحت مجلّدٍ واحد نُزع ذلك المجلّد.
- **الكتابة:** بدفتر الرجوع ملفًّا ملفًّا، فكلُّ ملفٍّ مستورَد قيدٌ يُعرف أصلُه، وما يعدّله الوكيلُ بعدها يُرجع عنه إلى المستورَد.

**التصديرُ حتميّ.** الملفّاتُ العادية تحت المجلّد بترتيبٍ ثابت وتاريخٍ ثابت، بلا المخفيّ ولا حالة ديوان الداخلية.
فالمجلّدُ نفسُه يعطي الأرشيفَ نفسَه بايتًا ببايت.
"""
from __future__ import annotations

import base64
import binascii
import io
import os
import stat
import unicodedata
import zipfile
import zlib
from pathlib import Path

from agent.journal import MAX_BYTES, Journal, JournalRefused, _directory, _open_directory, _read, _relative

MAX_ARCHIVE_BYTES = 360 * 1024          # الأرشيفُ مرمَّزًا base64 داخل حدّ الطلب (٥١٢ كيلوبايت)
MAX_FILES = 400
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_EXPORT_BYTES = 16 * 1024 * 1024
SKIPPED_DIRS = {"__MACOSX", "__pycache__", "node_modules", ".git", ".hg", ".svn", ".venv", "venv"}
_FIXED_TIME = (2026, 1, 1, 0, 0, 0)


class ArchiveRefused(ValueError):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{reason} [{code}]")
        self.code = code
        self.reason = reason


def _refuse(code, reason):
    raise ArchiveRefused(code, reason)


def folder_name(value) -> str:
    """اسمُ مجلّدٍ واحد في جذر المساحة: بلا فاصل ولا نقطةٍ في أوله ولا محارف تحكّم."""
    if (not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 80
            or "/" in value or "\\" in value or value.startswith(".")
            or any(unicodedata.category(c).startswith("C") for c in value)):
        _refuse("folder_invalid", "اسمُ مجلّدٍ واحد مطلوب، بلا فاصل ولا نقطةٍ في أوله")
    return value


def _entry_path(info: zipfile.ZipInfo) -> str | None:
    """مسارُ عنصرٍ في الأرشيف بعد فحصه، أو لا شيء لمجلّد."""
    name = info.filename
    # المسارُ المطلق يُردّ بمكوّنه الفارغ أدناه، فلا شرطَ له هنا
    if "\\" in name or "\x00" in name or (len(name) > 1 and name[1] == ":"):
        _refuse("archive_path_unsafe", f"مسارٌ غير آمن في الأرشيف: {name!r}")
    if stat.S_ISLNK(info.external_attr >> 16):
        _refuse("archive_path_unsafe", f"رابطٌ رمزيّ في الأرشيف: {name!r}")
    if name.endswith("/"):
        return None
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        _refuse("archive_path_unsafe", f"مسارٌ غير آمن في الأرشيف: {name!r}")
    if any(unicodedata.category(c).startswith("C") for c in name) or len(name.encode("utf-8")) > 1024:
        _refuse("archive_path_unsafe", f"محارفُ مسارٍ غير صالحة: {name!r}")
    return name


def read_archive(encoded: str) -> tuple[dict[str, bytes], list[str]]:
    """الملفّاتُ المقبولة (مسارٌ ← بايتات) وما تُخطّي بأسبابه. لا أثرَ على القرص."""
    if not isinstance(encoded, str) or len(encoded) > (MAX_ARCHIVE_BYTES * 4) // 3 + 4:
        _refuse("archive_too_large", f"الأرشيفُ فوق {MAX_ARCHIVE_BYTES // 1024} كيلوبايت")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        _refuse("archive_invalid", "ليس base64 صالحًا")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        infos = archive.infolist()
    except (zipfile.BadZipFile, ValueError, OSError):
        _refuse("archive_invalid", "ليس أرشيفَ zip صالحًا")
    files, skipped, total = {}, [], 0
    for info in infos:
        path = _entry_path(info)
        if path is None:
            continue
        parts = path.split("/")
        if any(p in SKIPPED_DIRS for p in parts[:-1]) or any(p.startswith(".") for p in parts):
            skipped.append(path)
            continue
        if info.flag_bits & 0x1:
            _refuse("archive_encrypted", f"ملفٌّ مشفَّر: {path}")
        if info.file_size > MAX_BYTES:
            _refuse("archive_file_too_large", f"{path}: فوق {MAX_BYTES // 1024} كيلوبايت")
        # zipfile لا يفكّ أكثرَ من الحجم المعلَن، والمعلَنُ فُحص قبله: فلا قنبلةَ ضغط. وحجمٌ معلَنٌ كاذب
        # يظهر بصمةَ CRC مخالفة، فيُردّ الأرشيفُ كلُّه
        try:
            with archive.open(info) as stream:
                data = stream.read()
        except (zipfile.BadZipFile, zlib.error, EOFError, OSError, NotImplementedError):
            _refuse("archive_corrupt", f"{path}: لا يُقرأ كما أُعلن")
        if unicodedata.normalize("NFC", path) in {unicodedata.normalize("NFC", p) for p in files}:
            _refuse("archive_path_duplicate", f"مسارٌ مكرَّر: {path}")
        total += len(data)
        if len(files) >= MAX_FILES:
            _refuse("archive_too_many_files", f"أكثرُ من {MAX_FILES} ملفّ")
        if total > MAX_TOTAL_BYTES:
            _refuse("archive_too_large", f"المجموعُ فوق {MAX_TOTAL_BYTES // (1024 * 1024)} ميغابايت")
        files[path] = data
    if not files:
        _refuse("archive_empty", "لا ملفَّ يُستورد في الأرشيف")
    tops = {p.split("/", 1)[0] for p in files}
    if len(tops) == 1 and all("/" in p for p in files):
        prefix = next(iter(tops)) + "/"
        files = {p[len(prefix):]: data for p, data in files.items()}
    return files, sorted(skipped)


def import_archive(workspace: Path, folder: str, encoded: str) -> dict:
    """يستورد الأرشيفَ إلى مجلّدٍ جديد في المساحة بدفتر الرجوع، بعد فحصه كلِّه."""
    folder = folder_name(folder)
    files, skipped = read_archive(encoded)
    workspace = Path(workspace)
    if os.path.lexists(workspace / folder):
        _refuse("import_target_exists", f"المجلّدُ {folder} قائمٌ في المساحة؛ اختر اسمًا جديدًا")
    # قواعدُ الدفتر نفسُها قبل أن يُكتب شيء. وهذا الفحصُ **زائدٌ اليوم**: كلُّ ما يردّه الدفترُ (المخفيُّ والمحميّ
    # ومحارفُ التحكّم) ردّه `read_archive` أو تخطّاه قبله، وأثبتت ذلك طفرةٌ حُذف فيها فبقيت الاختباراتُ خضراء.
    # يبقى عمقًا لا حارسًا: إن ضاقت قواعدُ الدفتر يومًا رُدّ الأرشيفُ كلُّه قبل الكتابة لا بعد نصفها.
    for path in files:
        try:
            _relative(f"{folder}/{path}", writing=True)
        except JournalRefused as exc:
            _refuse("archive_path_unsafe", f"{folder}/{path}: {exc.reason}")
    journal = Journal(workspace)
    actions = []
    try:
        for path, data in sorted(files.items()):
            actions.append(journal.write_bytes(f"{folder}/{path}", data).action_id)
    except JournalRefused as exc:
        # ما كُتب قبله يبقى مقيَّدًا في الدفتر، ويُسمّى العددُ فلا يُدّعى أن لا شيء كُتب
        _refuse(exc.code, f"{exc.reason} (كُتب {len(actions)} من {len(files)} قبل الرفض)")
    return {"status": "imported", "folder": folder, "files": len(files),
            "bytes": sum(len(d) for d in files.values()), "skipped": skipped, "journal_action_ids": actions}


def _regular_files(root: Path, prefix: str) -> list[tuple[str, bytes]]:
    out, total = [], 0
    fd = _open_directory(root)
    try:
        start = fd
        for part in prefix.split("/") if prefix else []:
            try:
                start = _directory(start, part)
            except (FileNotFoundError, NotADirectoryError):
                _refuse("export_folder_missing", f"لا مجلّدَ عند {prefix}")

        def walk(parent, base):
            nonlocal total
            for leaf in sorted(os.listdir(parent)):
                if leaf.startswith(".") or leaf in SKIPPED_DIRS:
                    continue
                info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = _directory(parent, leaf)
                    try:
                        walk(child, base + leaf + "/")
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    snapshot = _read(parent, leaf, MAX_EXPORT_BYTES)
                    if snapshot is None:
                        continue
                    total += len(snapshot[0])
                    if total > MAX_EXPORT_BYTES or len(out) >= 4 * MAX_FILES:
                        _refuse("export_too_large", "المجلّدُ أكبرُ من حدّ التصدير")
                    out.append((base + leaf, snapshot[0]))
        walk(start, "")
        if start != fd:
            os.close(start)
        return out
    finally:
        os.close(fd)


def export_archive(workspace: Path, folder: str = "") -> dict:
    """أرشيفُ zip حتميّ لمجلّدٍ في المساحة (أو لها كلِّها)، مرمَّزًا base64."""
    if folder:
        folder_name(folder)
    files = _regular_files(Path(workspace), folder)
    if not files:
        _refuse("export_empty", "لا ملفَّ يُصدَّر")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in files:
            info = zipfile.ZipInfo(path, date_time=_FIXED_TIME)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    raw = buffer.getvalue()
    return {"name": (folder or "workspace") + ".zip", "files": len(files),
            "archive": base64.b64encode(raw).decode("ascii"), "bytes": len(raw)}
