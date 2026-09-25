"""عقود الطلب والجواب.

ترتيب الحقول ليس تجميلًا: حقلٌ ذو قيمة افتراضية لا يجوز أن يتقدّم حقلًا
بلا قيمة، وإلا رُفع TypeError عند تعريف الفئة — وعقدٌ لا يُحمَّل ليس عقدًا.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Literal

# تصنيف البيانات: مجموعة مغلقة. الغائب والمجهول يُرفضان، ولا يُفترض أيّهما.
DATA_POLICIES = frozenset({"local_only", "regulated", "internal", "public"})
LOCAL_ONLY_POLICIES = frozenset({"local_only", "regulated"})

StopReason = Literal["complete", "max_output", "deadline", "error", "refused"]


# درجاتُ الإذن — ثلاثٌ لا محرّكَ سياسات: كلمةٌ واحدةٌ لكل أداة.
#   auto   — يُنفَّذ فورًا (قراءةٌ وبحثٌ: لا أثرَ خارج الذاكرة)
#   logged — يُنفَّذ ويُقيَّد (أثرٌ **رَجعيّ** داخل مساحة العمل)
#   owner  — ينتظر إذن المالك (أثرٌ لا يُردّ، أو خارج المساحة، أو شبكةٌ أو مال)
CONSENT_GRADES = ("auto", "logged", "owner")

TOOL_NAME = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
CALL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


@dataclass(frozen=True)
class ToolSpec:
    """أداةٌ مُعلَنة للنموذج: ما تفعل، وبأيّ وسائط، وبأيّ إذن.

    `reversible` ليست وصفًا بل تعهّدًا يُفرَض عند التنفيذ: أداةٌ تعلن
    الرَّجعية يلزمها أن تُودِع ما يكفي للرجوع **قبل** أن تُحدِث أثرها.
    وهي المفتاحُ العمليّ للاستقلال: ما يُردّ لا يحتاج إذنًا واقعةً بواقعة.
    """
    name: str
    description: str
    parameters: dict
    consent: str = "owner"       # الافتراضُ أضيقُ الدرجات، لا أوسعها
    reversible: bool = False     # والافتراضُ أنّ الأثر لا يُردّ

    def declared(self) -> dict:
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters, "consent": self.consent,
                "reversible": self.reversible}


@dataclass(frozen=True)
class ToolCall:
    """نداءُ أداةٍ طلبه النموذج — **مدخلٌ غير موثوق** يُفحص قبل التنفيذ."""
    call_id: str
    name: str
    arguments: dict

    def declared(self) -> dict:
        return {"call_id": self.call_id, "name": self.name,
                "arguments": self.arguments}


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    # نتيجةُ أداةٍ تُنسب إلى ندائها؛ وتبقى None في كل رسالةٍ أخرى فلا
    # تدخل البصمة — فبصماتُ ما قبل الأدوات تبقى كما هي حرفًا بحرف.
    tool_call_id: str | None = None
    # النداءات جزء من رسالة المساعد، وليست نصًا منفصلًا عن تاريخ الحوار.
    # يظهر الحقل في البصمة حين يوجد فقط لحفظ بصمات المحادثات النصية القديمة.
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Request:
    # الإلزاميّ أوّلًا
    messages: tuple[Message, ...]
    model: str
    model_version: str
    max_output: int
    deadline_s: float
    data_policy: str            # يُرفض الغائب والمجهول
    idempotency_key: str | None  # الانقطاع ليس إذنًا بإعادة الفعل
    tools: tuple[ToolSpec, ...] = ()

    def fingerprint_payload(self) -> dict:
        """ما يدخل البصمة — ولا يدخلها ما يتغيّر بين تشغيلين متكافئين.

        المهلة بيانات تشغيل عابرة فتُستبعد؛ والتصنيف والنموذج وإصداره
        وسقف الإخراج والأدوات والرسائل تدخل.
        """
        return {
            "messages": [_message_payload(m) for m in self.messages],
            "model": self.model,
            "model_version": self.model_version,
            "max_output": self.max_output,
            "data_policy": self.data_policy,
            "tools": [t.declared() for t in self.tools],
            "schema_version": 1,
        }


def _message_payload(m: Message) -> dict:
    """حقول الأدوات تظهر حين توجد فقط — فلا تنزاح بصمة رسالة نصية قديمة."""
    payload = {"role": m.role, "content": m.content}
    if m.tool_call_id is not None:
        payload["tool_call_id"] = m.tool_call_id
    if m.tool_calls:
        payload["tool_calls"] = [call.declared() for call in m.tool_calls]
    return payload


@dataclass(frozen=True)
class Response:
    content: str
    usage: Usage
    stop_reason: StopReason
    cost_micros: int                  # ميكرو-دولار صحيح، لا عشري
    retryable_error: str | None = None
    provider: str = ""
    model_version: str = ""
    # نداءاتُ أدواتٍ طلبها النموذج. الفراغُ هو الحال الطبيعية، ومزوّدٌ
    # لم تُعلَن له أدواتٌ يبقى مُلزَمًا بردّ أيّ نداءٍ يأتيه (فشلٌ مغلق).
    tool_calls: tuple[ToolCall, ...] = ()
