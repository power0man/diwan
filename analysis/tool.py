"""أداةُ `analyze_data` (ج٨): تحليلٌ بكود بايثون في حاويةٍ معزولة، ونتائجُه ملفّاتٌ بإيصال.

- **الدرجة `logged`:** أثرُها الوحيد في المساحة كتابةُ المخرجات المعلنة بدفتر الرجوع. فلا تنتظر إذنًا في كل
  نداء، ويُرجع عن النداء كلِّه بزرٍّ واحد (مجموعةُ قيود، `agent/action_revert.py`).
- **الكلُّ أو لا شيء:** لا يُكتب مخرَجٌ إلا إن خرج الكودُ بصفرٍ في مهلته وأنتج كلَّ ما أعلنه بحدوده.
  وغيرُ ذلك رفضٌ مسمًّى يحمل ذيلَ الخطأ ليصحّح النموذجُ كوده.
- **البياناتُ لا تعليمات:** ما يطبعه الكودُ ومعاينةُ المخرجات النصّية يعودان إلى النموذج عبر حَجر نتائج
  الأدوات في الحلقة (ك٢٣).
- **لا تُعلَن إلا حيث ضُبطت صورتُها** (`configure_analysis_backend` في الإقلاع الموثوق). وجلسةٌ محفوظةٌ
  أعلنتها تُفتح بعد ذلك بلا صورة، فيُرفض النداءُ باسمه (`analysis_backend_unavailable`).
"""
from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from agent.builtin_tools import _inside
from agent.journal import JournalRefused
from agent.registry import Tool, ToolRefused
from analysis.backend import (DEFAULT_TIMEOUT_S, MAX_INPUTS, MAX_OUTPUTS, MAX_TIMEOUT_S, ExecutionRefused,
                              analysis_backend)
from core.contracts import ToolSpec

TEXT_SUFFIXES = frozenset({".csv", ".tsv", ".json", ".txt", ".md"})
PREVIEW_CHARS = 1500
FAILURE_TAIL = 3000


def _strings(value, name, *, upper, lower=0) -> list[str]:
    if not isinstance(value, list) or not lower <= len(value) <= upper or not all(isinstance(v, str) for v in value):
        raise ToolRefused("argument_invalid", f"الوسيط «{name}» قائمةٌ نصّية من {lower} إلى {upper}")
    return value


def _failure(code: str, reason: str, result) -> ToolRefused:
    detail = "\n".join(part for part in (
        f"stdout:\n{result.stdout[-FAILURE_TAIL:]}" if result.stdout.strip() else "",
        f"stderr:\n{result.stderr[-FAILURE_TAIL:]}" if result.stderr.strip() else "") if part)
    return ToolRefused(code, reason + ("\n" + detail if detail else "") + "\nلم يُكتب شيء.")


def _analyze_data(arguments, context):
    code = arguments.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ToolRefused("argument_invalid", "الوسيط «code» كودُ بايثون غير فارغ")
    inputs = _strings(arguments.get("inputs", []), "inputs", upper=MAX_INPUTS)
    outputs = _strings(arguments.get("outputs"), "outputs", upper=MAX_OUTPUTS, lower=1)
    timeout_s = arguments.get("timeout_s", DEFAULT_TIMEOUT_S)
    if type(timeout_s) is not int or not 1 <= timeout_s <= MAX_TIMEOUT_S:
        raise ToolRefused("argument_invalid", f"الوسيط «timeout_s» عددٌ صحيح من 1 إلى {MAX_TIMEOUT_S}")
    for path in inputs:
        _inside(context, path)
    for path in outputs:                          # قبل التشغيل: لا تُشغَّل حاويةٌ لمخرجٍ مرفوض
        _inside(context, path, writing=True)
    try:
        result = analysis_backend(context.root).analyze(code, inputs, outputs, timeout_s=timeout_s)
    except ExecutionRefused as exc:
        raise ToolRefused(exc.code, exc.reason) from None
    if result.timed_out:
        raise _failure("analysis_timed_out", f"تجاوز الكودُ مهلته ({timeout_s} ثانية).", result)
    if result.exit_code != 0:
        raise _failure("analysis_script_failed", f"خرج الكودُ برمز {result.exit_code}.", result)
    if result.missing:
        raise _failure("analysis_outputs_missing", "لم يُنتج الكودُ: " + "، ".join(result.missing) + ".", result)
    if result.oversized:
        raise _failure("analysis_output_too_large", "مخرجاتٌ فوق حدّ الحجم: " + "، ".join(result.oversized) + ".",
                       result)
    written, action_ids = [], []
    try:
        for path in outputs:
            raw = result.outputs[path]
            action = context.journal.write_bytes(path, raw)
            action_ids.append(action.action_id)
            written.append({"path": action.path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    except JournalRefused:
        # تعذّرت كتابةُ مخرجٍ بعد غيره: يُرجع عمّا كُتب، فلا يبقى نصفُ نتيجةٍ بلا زرّ رجوع
        for action_id in reversed(action_ids):
            try:
                context.journal.revert(action_id)
            except JournalRefused:
                pass
        raise
    lines = ["حُلِّل في حاويةٍ معزولة، وكُتب بإيصالٍ يُرجع عنه:"]
    lines += [f"- {item['path']} ({item['bytes']} بايت)" for item in written]
    if result.stdout.strip():
        lines += ["", "ما طبعه الكود:", result.stdout[-4000:]]
    for item in written:
        if PurePosixPath(item["path"]).suffix.casefold() in TEXT_SUFFIXES:
            text = result.outputs[item["path"]].decode("utf-8", "replace")
            shown = text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "\n[…المعاينةُ مبتورة]"
            lines += ["", f"معاينة {item['path']}:", shown]
    return {"content": "\n".join(lines), "action_ids": action_ids, "written": written,
            "boundary": result.boundary}


ANALYZE_DATA = Tool(ToolSpec(
    "analyze_data",
    "يحلّل بياناتٍ بكود بايثون (pandas وnumpy وopenpyxl وmatplotlib) في حاويةٍ معزولة بلا شبكة. "
    "تُنسخ إليها ملفّاتُ المساحة المسمّاة في inputs بمساراتها النسبية نفسِها، ويُكتب ما في outputs وحده "
    "إلى المساحة بإيصالٍ يُرجع عنه، ولا يُكتب شيءٌ إن فشل الكود أو نقص مخرج. "
    "المخرجات: csv وtsv وjson وtxt وmd وxlsx وpng. وللعربية في الرسوم: from diwan_ar import ar ثم ar(\"نص\").",
    {"type": "object", "properties": {
        "code": {"type": "string"},
        "inputs": {"type": "array", "items": {"type": "string"}},
        "outputs": {"type": "array", "items": {"type": "string"}},
        "timeout_s": {"type": "integer"}},
     "required": ["code", "outputs"]},
    consent="logged", reversible=True), _analyze_data)
