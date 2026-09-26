"""ثماني أدواتٍ أولى — درجةُ كلٍّ منها تتبع أثرَها لا خطورةَ اسمها.

  auto   — لا أثرَ يبقى بعد النداء (قراءةٌ وبحثٌ وسردٌ واختبارات).
  logged — أثرٌ يبقى وهو **رَجعيّ** بدفتر الرجوع (كتابةٌ داخل المساحة).
  owner  — أثرٌ لا يُردّ أو يعبر حدًّا (تشغيلُ أمرٍ من تأليف النموذج).

الأوامر والاختبارات كلاهما ينفذ شيفرة مرشحة داخل حاوية موثقة فقط.
إعلانُ متغير بيئة أو موافقةُ النداء لا يثبت وجود حد تنفيذ.
"""
from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
import re

from core.contracts import ToolSpec
from core.execution import execute_candidate
from documents.export import FORMATS, ExportRefused, export
from agent.registry import Tool, ToolContext, ToolRefused
from workspace_tools.files import _relative

MAX_MATCHES = 60
MAX_LISTED = 300
MAX_READ_BYTES = 131_072
SKIP_DIRS = {".git", ".diwan-journal", "__pycache__", "node_modules", ".venv", "var"}


def _need(arguments: dict, key: str, kind=str):
    value = arguments.get(key)
    if not isinstance(value, kind) or (kind is str and not value.strip()):
        raise ToolRefused("argument_invalid", f"الوسيط «{key}» مطلوبٌ من نوع {kind.__name__}")
    return value


def _inside(context: ToolContext, relative: str, *, writing=False) -> Path:
    relative = _relative(relative, writing=writing)
    root = context.root.resolve()
    target = (root / relative)
    probe = target if target.exists() else target.parent
    resolved = probe.resolve() if probe.exists() else probe
    if not (resolved == root or str(resolved).startswith(str(root) + os.sep)):
        raise ToolRefused("path_escapes_root", "المسار يخرج من مساحة العمل")
    return target


def _walk(root: Path):
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            path = Path(base) / name
            if not path.is_symlink() and path.is_file():
                yield path


# ————— auto: لا أثرَ يبقى —————

def _read_file(arguments, context):
    path = _inside(context, _need(arguments, "path"))
    if path.is_symlink():
        raise ToolRefused("path_symlink", "لا يُقرأ عبر رابط رمزيّ")
    if not path.is_file():
        raise ToolRefused("file_not_found", f"لا ملفَّ عند {arguments['path']}")
    raw = path.read_bytes()[:MAX_READ_BYTES]
    try:
        return {"content": raw.decode("utf-8"), "bytes": len(raw)}
    except UnicodeDecodeError:
        raise ToolRefused("file_not_text", "الملفُّ ليس نصًّا بترميز UTF-8")


def _search_files(arguments, context):
    pattern = _need(arguments, "pattern")
    try:
        needle = re.compile(pattern)
    except re.error as exc:
        raise ToolRefused("pattern_invalid", f"نمطٌ غير صالح: {exc}")
    suffix = arguments.get("suffix")
    if suffix is not None and not isinstance(suffix, str):
        raise ToolRefused("argument_invalid", "الوسيط «suffix» نصّ")
    hits, scanned = [], 0
    for path in _walk(context.root.resolve()):
        if suffix and not path.name.endswith(suffix):
            continue
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if needle.search(line):
                hits.append(f"{path.relative_to(context.root.resolve())}:{number}: {line.strip()[:200]}")
                if len(hits) >= MAX_MATCHES:
                    return {"content": "\n".join(hits), "matches": len(hits),
                            "complete": False, "files_scanned": scanned}
    return {"content": "\n".join(hits) or "لا مطابقات.", "matches": len(hits),
            "complete": True, "files_scanned": scanned}


def _list_files(arguments, context):
    prefix = arguments.get("prefix") or ""
    if not isinstance(prefix, str):
        raise ToolRefused("argument_invalid", "الوسيط «prefix» نصّ")
    root = context.root.resolve()
    base = _inside(context, prefix) if prefix else root
    if not base.is_dir():
        raise ToolRefused("directory_not_found", f"لا مجلَّد عند {prefix!r}")
    names = [str(p.relative_to(root)) for p in _walk(base)]
    return {"content": "\n".join(names[:MAX_LISTED]) or "لا ملفات.",
            "count": len(names), "complete": len(names) <= MAX_LISTED}


def _run_tests(arguments, context):
    paths = arguments.get("paths") or ["tests/"]
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise ToolRefused("argument_invalid", "الوسيط «paths» قائمةُ مساراتٍ نصّية")
    if len(paths) > 20:
        raise ToolRefused("argument_invalid", "عشرون مسارًا على الأكثر")
    # ثغرةٌ اصطادها اختبارُ هذه الأداة: `_relative` يقبل «-p» و«--co» لأنهما
    # أسماءُ ملفاتٍ نسبيّةٌ صالحة. فكان النموذج يستطيع حقنَ رايةٍ تُحمّل
    # مكوّنَ pytest من اختياره — أي تنفيذُ شيفرةٍ عشوائية بدرجة «auto».
    # فالحدُّ ثلاثيّ: لا يبدأ بشرطة، وتحت `tests/` وحدها، وموجودٌ فعلًا.
    safe = []
    for item in paths:
        stripped = item.rstrip("/")
        # هذا الشرطُ **زائدٌ اليوم**: شرطُ البادئة `tests/` أدناه يردّ «-p»
        # وحدَه — أُثبت ذلك بطفرةٍ حُذف فيها هذا السطر فبقيت الاختباراتُ
        # خضراء. يبقى عمقًا لا حارسًا: إن رُخِّصت البادئةُ يومًا بقي الردّ.
        if stripped.startswith("-"):
            raise ToolRefused("argument_invalid", f"رايةٌ لا مسار: {item!r}")
        _relative(stripped)
        if stripped != "tests" and not stripped.startswith("tests/"):
            raise ToolRefused("argument_invalid",
                              f"لا تُشغَّل إلا اختباراتُ المشروع تحت tests/: {item!r}")
        if not (context.root.resolve() / stripped).exists():
            raise ToolRefused("path_not_found", f"لا هدفَ عند {item!r}")
        safe.append(stripped)
    # مفسّر الصورة المقيدة، لا مفسّر المضيف ولا البحث في PATH.
    argv = ("/opt/venv/bin/python", "-m", "pytest", "-q", "-p", "no:cacheprovider", *safe)
    proc = execute_candidate(argv, context.root, timeout_s=900)
    tail = (proc.stdout or "")[-4000:] + (proc.stderr or "")[-2000:]
    return {"content": tail, "exit_code": proc.exit_code,
            "passed": proc.exit_code == 0, "boundary": proc.boundary,
            "output_truncated": proc.output_truncated or len(proc.stdout) > 4000 or len(proc.stderr) > 2000}


# ————— logged: أثرٌ رَجعيّ —————

def _write_file(arguments, context):
    path = _need(arguments, "path")
    content = arguments.get("content")
    if not isinstance(content, str):
        raise ToolRefused("argument_invalid", "الوسيط «content» نصّ")
    _inside(context, path, writing=True)
    action = context.journal.write_file(path, content)
    return {"content": f"كُتب {action.path} ({len(content.encode('utf-8'))} بايت). "
                       f"للرجوع: {action.action_id}",
            "action_id": action.action_id, "reverts_to":
                "غير موجود" if action.before_sha256 is None else action.before_sha256[:12]}


MAX_DIFF_CHARS = 8000


def _edit_file(arguments, context):
    """تحريرٌ في موضع الملف (ج١١): فرقٌ يُعرض، ويُطبَّق بالدفتر، ويُرجع عنه بزرّ الرجوع نفسِه."""
    path = _need(arguments, "path")
    old, new = arguments.get("old_text"), arguments.get("new_text")
    if not isinstance(old, str) or not old:
        raise ToolRefused("argument_invalid", "الوسيط «old_text» نصٌّ غير فارغ يرد في الملف مرّةً واحدة")
    if not isinstance(new, str):
        raise ToolRefused("argument_invalid", "الوسيط «new_text» نصّ")
    _inside(context, path, writing=True)
    action, before, after = context.journal.edit_file(path, old, new)
    diff = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                        fromfile=f"{action.path} (قبل)", tofile=f"{action.path} (بعد)"))
    shown = diff if len(diff) <= MAX_DIFF_CHARS else diff[:MAX_DIFF_CHARS] + "\n[…الفرقُ مبتور للعرض]"
    return {"content": f"عُدّل {action.path} في موضعه. للرجوع: {action.action_id}\n{shown}",
            "action_id": action.action_id, "diff": shown,
            "reverts_to": action.before_sha256[:12]}


def _export_document(arguments, context):
    """تصديرُ مستندٍ باتّجاهٍ عربيّ (ج١٠): docx أو xlsx بحسب امتداد المسار، ويُرجع عنه بالدفتر."""
    path = _need(arguments, "path")
    fmt = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if fmt not in FORMATS:
        raise ToolRefused("format_unsupported", f"امتدادُ المسار أحدُ: {'، '.join(FORMATS)}")
    try:
        raw = export(arguments.get("document"), fmt)
    except ExportRefused as exc:
        raise ToolRefused(exc.code, exc.reason) from None
    _inside(context, path, writing=True)
    action = context.journal.write_bytes(path, raw)
    return {"content": f"صُدِّر {action.path} ({fmt}، {len(raw)} بايت، من اليمين إلى اليسار). للرجوع: {action.action_id}",
            "action_id": action.action_id, "format": fmt, "bytes": len(raw),
            "reverts_to": "غير موجود" if action.before_sha256 is None else action.before_sha256[:12]}


# ————— owner: لا يُردّ —————

def _run_command(arguments, context):
    argv = arguments.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise ToolRefused("argument_invalid", "الوسيط «argv» قائمةٌ نصّية غير فارغة")
    if len(argv) > 40:
        raise ToolRefused("argument_invalid", "أربعون وسيطًا على الأكثر")
    proc = execute_candidate(tuple(argv), context.root, timeout_s=300)
    return {"content": (proc.stdout or "")[-6000:] + (proc.stderr or "")[-2000:],
            "exit_code": proc.exit_code, "boundary": proc.boundary,
            "output_truncated": proc.output_truncated or len(proc.stdout) > 6000 or len(proc.stderr) > 2000}


READ_FILE = Tool(ToolSpec(
    "read_file", "يقرأ ملفًّا نصّيًّا داخل مساحة العمل ويعيد محتواه.",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    consent="auto"), _read_file)

SEARCH_FILES = Tool(ToolSpec(
    "search_files", "يبحث بنمطٍ منتظم في ملفات مساحة العمل ويعيد المواضع.",
    {"type": "object", "properties": {"pattern": {"type": "string"},
                                      "suffix": {"type": "string"}},
     "required": ["pattern"]}, consent="auto"), _search_files)

LIST_FILES = Tool(ToolSpec(
    "list_files", "يسرد ملفات مساحة العمل، أو ما تحت بادئةٍ منها.",
    {"type": "object", "properties": {"prefix": {"type": "string"}}},
    consent="auto"), _list_files)

RUN_TESTS = Tool(ToolSpec(
    "run_tests", "يشغّل اختبارات المشروع بحزمةٍ ثابتة ويعيد ذيلَ الناتج وحالةَ الخروج.",
    {"type": "object", "properties": {"paths": {"type": "array",
                                                "items": {"type": "string"}}}},
    consent="auto"), _run_tests)

WRITE_FILE = Tool(ToolSpec(
    "write_file", "يكتب ملفًّا داخل مساحة العمل، ويودِع ما قبله فيُمكن الرجوع.",
    {"type": "object", "properties": {"path": {"type": "string"},
                                      "content": {"type": "string"}},
     "required": ["path", "content"]}, consent="logged", reversible=True), _write_file)

EDIT_FILE = Tool(ToolSpec(
    "edit_file", "يعدّل ملفًّا قائمًا في موضعه: يستبدل مقطعًا يرد فيه مرّةً واحدة، ويودِع ما قبله "
    "فيُمكن الرجوع، ويعيد الفرق.",
    {"type": "object", "properties": {"path": {"type": "string"},
                                      "old_text": {"type": "string"},
                                      "new_text": {"type": "string"}},
     "required": ["path", "old_text", "new_text"]}, consent="logged", reversible=True), _edit_file)

EXPORT_DOCUMENT = Tool(ToolSpec(
    "export_document", "يصدّر مستندًا باتّجاهٍ عربيّ إلى docx أو xlsx داخل مساحة العمل (بحسب امتداد المسار)، "
    "ويودِع ما قبله فيُمكن الرجوع. المستند: عنوانٌ وكتلٌ من heading وparagraph وlist وtable.",
    {"type": "object", "properties": {
        "path": {"type": "string"},
        "document": {"type": "object", "properties": {
            "title": {"type": "string"},
            "blocks": {"type": "array", "items": {"type": "object", "properties": {
                "type": {"type": "string", "enum": ["heading", "paragraph", "list", "table"]},
                "text": {"type": "string"}, "level": {"type": "integer"},
                "items": {"type": "array", "items": {"type": "string"}},
                "rows": {"type": "array", "items": {"type": "array"}},
                "header": {"type": "boolean"}, "name": {"type": "string"}},
                "required": ["type"]}}},
            "required": ["blocks"]}},
     "required": ["path", "document"]}, consent="logged", reversible=True), _export_document)

RUN_COMMAND = Tool(ToolSpec(
    "run_command", "يشغّل أمرًا في مساحة العمل — أثرُه لا يُردّ، فينتظر إذن المالك.",
    {"type": "object", "properties": {"argv": {"type": "array",
                                               "items": {"type": "string"}}},
     "required": ["argv"]}, consent="owner"), _run_command)

DEFAULT_TOOLS = (READ_FILE, SEARCH_FILES, LIST_FILES, RUN_TESTS, WRITE_FILE, EDIT_FILE, EXPORT_DOCUMENT, RUN_COMMAND)


def get_sovereign_tools() -> tuple[Tool, ...]:
    """استرجاع الأدوات السيادية المحكومة (م١٦): استرجاع الأنظمة والتحليل الصرفي."""
    from core.tools_registry import export_agent_tools
    return export_agent_tools()


def get_all_tools() -> tuple[Tool, ...]:
    """استرجاع الحزمة الكاملة المدمجة: أدوات مساحة العمل الافتراضية مع الأدوات السيادية."""
    return DEFAULT_TOOLS + get_sovereign_tools()
