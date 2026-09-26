"""وضعُ المبرمج (ج٩): تعليماتُ الجولة البرمجية وأدواتُها، وفرقُ الجولة من دفتر الرجوع.

الجلسةُ جلسةٌ وكيلة على مساحة المشروع نفسِها، بأدوات الشيفرة وحدها: القراءةُ والبحثُ والسردُ والكتابةُ والتحرير،
وrun_tests وrun_command حيث ضُبط التنفيذ. ولا بحثَ في الويب ولا ذاكرةَ ولا تصدير، فما يُقاس هو المبرمج.

**التعليماتُ مسجَّلةٌ ببصمتها** (`tests/test_coder_mode.py`)، وبها يُقاس بنكُ ك٤٤ (`--mode coder`)، ولا تُعدَّل بعد
ظهور نتيجةٍ عليه. فتعديلُها لأجل الرقم ضبطٌ على الاختبار.
"""
from __future__ import annotations

import difflib

from agent.loop import SYSTEM

CODER_SYSTEM = SYSTEM + (
    "\n\nهذه جلسةُ مبرمج: تعمل في مشروعٍ برمجيّ داخل مساحة العمل بأدوات الملفّات والاختبار.\n"
    "- اقرأ الملفّاتِ ذاتَ الصلة قبل أن تعدّل، وابحث عن مواضع الاستعمال قبل أن تغيّر اسمًا أو توقيعًا.\n"
    "- عدّل بأصغر تغييرٍ يحقّق المطلوب، وبـedit_file حيث يكفي، ولا تُعِد كتابةَ ملفٍّ كاملٍ بلا حاجة.\n"
    "- لا تعدّل الاختبارات ولا ملفّاتِ الإعداد إلا إن طُلب ذلك صراحةً. وإن وصف التوثيقُ سلوكًا فالتزمه كلَّه، "
    "لا ما يختبره الاختبارُ وحده.\n"
    "- إن أُتيحت run_tests فشغّل الاختبارات بعد التعديل، وأبلغ نتيجتَها كما هي.\n"
    "- إن طُلب جوابٌ في ملفّ فاكتبه بالصيغة المطلوبة حرفيًّا، ولا تعدّل غيرَه.\n"
    "- في آخر الجولة اذكر ما غيّرتَه ملفًّا ملفًّا، وما لم تستطع التحقّقَ منه."
)

# أدواتُ الشيفرة من الأدوات الافتراضية؛ والتنفيذُ منها لا يُعلَن إلا حيث ضُبطت خُلفيّتُه
CODER_TOOLS = ("read_file", "search_files", "list_files", "write_file", "edit_file", "run_tests", "run_command")
EXECUTION_TOOLS = frozenset({"run_tests", "run_command"})

MAX_FILE_DIFF_CHARS = 12_000
MAX_TURN_FILES = 32


def coder_tools(default_tools, *, execution: bool) -> list:
    """أدواتُ المبرمج بترتيب CODER_TOOLS، بلا أدوات التنفيذ حيث لم يُضبط."""
    by_name = {tool.spec.name: tool for tool in default_tools}
    return [by_name[name] for name in CODER_TOOLS
            if name in by_name and (execution or name not in EXECUTION_TOOLS)]


def _text(raw: bytes | None) -> str | None:
    if raw is None:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def file_diff(path: str, before: bytes | None, after: bytes | None) -> dict:
    """فرقُ ملفٍّ واحد بين حالَين: أُضيف أو حُذف أو عُدّل، وفرقٌ موحَّد إن كان نصًّا."""
    status = "added" if before is None else "removed" if after is None else "modified"
    old, new = _text(before), _text(after)
    if old is None or new is None:
        return {"path": path, "status": status, "binary": True, "diff": ""}
    diff = "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                        fromfile=f"{path} (قبل)", tofile=f"{path} (بعد)"))
    truncated = len(diff) > MAX_FILE_DIFF_CHARS
    return {"path": path, "status": status, "binary": False,
            "diff": diff[:MAX_FILE_DIFF_CHARS] + ("\n[…الفرقُ مبتور للعرض]" if truncated else ""),
            "truncated": truncated}
