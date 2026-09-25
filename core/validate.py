"""المدخل الإلزامي الوحيد للتحقّق.

كل طلبٍ يمرّ من هنا **قبل** اختيار المزوّد، و**قبل** حجز الكلفة،
و**قبل** البصم. ووحدةُ لغة النواة ليست دليلًا على سلامة المدخلات.

والقاعدة الحاكمة: **يُرفض الغائب والمجهول ولا يُفترض أيّهما.** فالقيدُ
الذي يفشل مفتوحًا ليس قيدًا.
"""
from __future__ import annotations

import math

from core.canonical import PayloadRejected, check_payload
from core.contracts import (CALL_ID, CONSENT_GRADES, DATA_POLICIES,
                            Message, Request, TOOL_NAME, ToolCall, ToolSpec)

MAX_OUTPUT_CEILING = 1_000_000
MAX_DEADLINE_S = 3600.0


def _reject(field: str, code: str, reason: str):
    raise PayloadRejected(f"request.{field}", code, reason)


def validated(request: Request) -> Request:
    """يعيد الطلب نفسه إن جاز، وإلا رمى PayloadRejected برمزٍ مُسمّى."""
    if not isinstance(request, Request):
        _reject("", "not_a_request", f"نوع غير مدعوم: {type(request).__name__}")

    # التصنيف: مجموعة مغلقة، والغائب والمجهول مرفوضان
    if not isinstance(request.data_policy, str):
        # بنيةٌ أو قائمة من إعدادٍ أو JSON: تُرفض ولا تُكسر فحص العضوية
        _reject("data_policy", "data_policy_type", "تصنيف البيانات نصّ")
    if request.data_policy == "":
        _reject("data_policy", "data_policy_missing", "تصنيف البيانات غائب")
    if request.data_policy not in DATA_POLICIES:
        _reject("data_policy", "data_policy_unknown",
                f"تصنيف غير معروف: {request.data_policy!r}")

    # النموذج وإصداره
    for f in ("model", "model_version"):
        v = getattr(request, f)
        if not isinstance(v, str) or not v.strip():
            _reject(f, f"{f}_missing", "قيمة نصّية غير فارغة مطلوبة")

    # الرسائل
    if not isinstance(request.messages, tuple) or not request.messages:
        _reject("messages", "messages_empty", "رسالة واحدة على الأقل، في tuple")
    for i, m in enumerate(request.messages):
        if not isinstance(m, Message):
            _reject(f"messages[{i}]", "message_type", "عنصر ليس Message")
        if m.role not in ("system", "user", "assistant", "tool"):
            _reject(f"messages[{i}].role", "role_unknown", f"دور غير معروف: {m.role!r}")
        if not isinstance(m.content, str):
            _reject(f"messages[{i}].content", "content_type", "المحتوى نصّ")

    # طلبُ التفكير: منطقيٌّ صريح، فلا يُقرأ «"no"» أو 1 طلبًا (ك٤٧)
    if not isinstance(request.thinking, bool):
        _reject("thinking", "thinking_type", "طلبُ التفكير True أو False")

    # سقف الإخراج: صحيحٌ موجب داخل حدّ
    mo = request.max_output
    if isinstance(mo, bool) or not isinstance(mo, int):
        _reject("max_output", "max_output_type", "عدد صحيح مطلوب")
    if mo <= 0:
        _reject("max_output", "max_output_not_positive", f"قيمة غير موجبة: {mo}")
    if mo > MAX_OUTPUT_CEILING:
        _reject("max_output", "max_output_too_large", f"فوق الحدّ: {mo}")

    # المهلة: عددٌ منتهٍ موجب — واللانهاية تجتاز ">= 0" فتُمنع صراحةً
    dl = request.deadline_s
    if isinstance(dl, bool) or not isinstance(dl, (int, float)):
        _reject("deadline_s", "deadline_type", "عدد مطلوب")
    if not math.isfinite(dl):
        _reject("deadline_s", "deadline_not_finite", f"قيمة غير منتهية: {dl}")
    if dl <= 0:
        _reject("deadline_s", "deadline_not_positive", f"قيمة غير موجبة: {dl}")
    if dl > MAX_DEADLINE_S:
        _reject("deadline_s", "deadline_too_large", f"فوق الحدّ: {dl}")

    # الأدوات — تُعلَن بعقدها لا باسمها وحده
    if not isinstance(request.tools, tuple):
        _reject("tools", "tools_type", "tuple مطلوب")
    seen_tools = set()
    for i, t in enumerate(request.tools):
        if not isinstance(t, ToolSpec):
            _reject(f"tools[{i}]", "tool_spec_type", "ToolSpec مطلوب")
        if not isinstance(t.name, str) or not TOOL_NAME.fullmatch(t.name):
            _reject(f"tools[{i}].name", "tool_name", "اسمٌ صغيرُ الحروف بلا مسارات")
        if t.name in seen_tools:
            _reject(f"tools[{i}].name", "tool_duplicate", f"أداةٌ مكرَّرة: {t.name}")
        seen_tools.add(t.name)
        if not isinstance(t.description, str) or not t.description.strip():
            _reject(f"tools[{i}].description", "tool_description",
                    "وصفٌ غير فارغ: النموذج يختار به")
        if not isinstance(t.parameters, dict):
            _reject(f"tools[{i}].parameters", "tool_parameters", "كائنُ وسائط مطلوب")
        if t.consent not in CONSENT_GRADES:
            _reject(f"tools[{i}].consent", "tool_consent",
                    f"درجةُ إذنٍ من {CONSENT_GRADES}")
        if type(t.reversible) is not bool:
            _reject(f"tools[{i}].reversible", "tool_reversible", "قيمة منطقية مطلوبة")
        # تعهّدٌ لا وصف: أداةٌ بإذنٍ مُسجَّل يلزمها أن تكون رَجعية، وإلّا
        # فدرجتُها owner. وهذا هو الشرطُ الذي يشتري العملَ بلا تدخّل.
        if t.consent == "logged" and not t.reversible:
            _reject(f"tools[{i}]", "tool_consent_requires_reversible",
                    "الإذنُ المُسجَّل لا يُمنح لأثرٍ لا يُردّ")

    # سجل الطلب مغلق: كل نداء معلن في رسالة مساعد له نتيجة واحدة قبل
    # الرسالة التالية. قد يكتمل ترتيب نتائج الدفعة بأي ترتيب، لكن لا يتيم
    # ولا تكرار ولا طلب إلى المزود مع أفعال لم تُسوَّ بعد.
    seen_calls, answered, pending = set(), set(), set()
    for i, m in enumerate(request.messages):
        if not isinstance(m.tool_calls, tuple):
            _reject(f"messages[{i}].tool_calls", "tool_calls_type", "نداءات الأدوات tuple")
        if m.tool_calls and m.role != "assistant":
            _reject(f"messages[{i}].tool_calls", "tool_calls_unexpected",
                    "رسالة المساعد وحدها تعلن نداءات الأدوات")
        if m.role == "tool":
            if not isinstance(m.tool_call_id, str) or not CALL_ID.fullmatch(m.tool_call_id):
                _reject(f"messages[{i}].tool_call_id", "tool_call_id_required",
                        "نتيجةُ أداةٍ بلا نداءٍ تُنسب إليه")
            if m.tool_call_id in answered:
                _reject(f"messages[{i}].tool_call_id", "tool_result_duplicate", "نتيجة مكررة لنداء واحد")
            if m.tool_call_id not in pending:
                _reject(f"messages[{i}].tool_call_id", "tool_result_orphan", "نتيجة بلا نداء سابق معلق مطابق")
            pending.remove(m.tool_call_id)
            answered.add(m.tool_call_id)
        elif m.tool_call_id is not None:
            _reject(f"messages[{i}].tool_call_id", "tool_call_id_unexpected",
                    "لا يُنسب إلى نداءِ أداةٍ إلا رسالةُ أداة")
        elif pending:
            _reject(f"messages[{i}]", "tool_results_pending", "لا رسالة جديدة قبل تسوية نتائج دفعة الأدوات")
        for j, call in enumerate(m.tool_calls):
            field = f"messages[{i}].tool_calls[{j}]"
            if not isinstance(call, ToolCall):
                _reject(field, "tool_call_type", "عنصر ليس ToolCall")
            if not isinstance(call.call_id, str) or not CALL_ID.fullmatch(call.call_id):
                _reject(field + ".call_id", "tool_call_id_invalid", "معرف نداء غير صالح")
            if call.call_id in seen_calls:
                _reject(field + ".call_id", "tool_call_id_duplicate", "معرف نداء مكرر في التاريخ")
            if not isinstance(call.name, str) or not TOOL_NAME.fullmatch(call.name):
                _reject(field + ".name", "tool_name", "اسم أداة غير صالح")
            if not isinstance(call.arguments, dict):
                _reject(field + ".arguments", "tool_call_arguments", "وسائط الأداة كائن")
            # لا نشترط وجود الأداة التاريخية في مجموعة الأدوات المتاحة الآن.
            check_payload(call.declared(), "request." + field)
            seen_calls.add(call.call_id)
            pending.add(call.call_id)
    if pending:
        _reject("messages", "tool_results_pending", "طلب المزود لا يحمل نداءات بلا نتائج")

    # مفتاح عدم التكرار
    k = request.idempotency_key
    if k is not None and (not isinstance(k, str) or not k.strip()):
        _reject("idempotency_key", "idempotency_key_blank", "إمّا None أو نصّ غير فارغ")

    # وأخيرًا: ما سيُبصم يجب أن يُبصم
    check_payload(request.fingerprint_payload(), "request.fingerprint_payload")
    return request
