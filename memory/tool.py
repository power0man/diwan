"""أداةُ `propose_memory` (ك٥٥، `docs/MEMORY-DESIGN.md` §٣.٢): الطريقُ الثاني للحفظ.

درجتُها `owner`، ودرجةُ المالك لا تُمنح ميثاقًا (`agent/registry.py`): فالنداءُ يقف
`awaiting_owner` بإيصالٍ مربوطٍ ببصمته، ويرى المالكُ النصَّ المقترح بعينه في بطاقة القرار.
ولا يجري المعالِجُ إلا بعد قبول ذلك النداء نفسِه (`agent/actions.py`)، فحفظُه بموافقة المالك
حقيقةٌ يضمنها مخزنُ الأفعال لا ادّعاءٌ من المعالِج.
"""
from __future__ import annotations

from agent.registry import Tool, ToolRefused
from core.contracts import ToolSpec
from memory.store import MemoryRefused

PROPOSE_MEMORY_SPEC = ToolSpec(
    "propose_memory",
    "يقترح على المالك حفظَ معلومةٍ في ذاكرة هذا المشروع لتُذكر في جولاتٍ لاحقة. "
    "لا يُحفظ شيءٌ حتى يقبل المالك، فاقترح ما يفيد لاحقًا وحده.",
    {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    consent="owner")


def propose_memory_tool(open_store) -> Tool:
    """`open_store` يفتح مخزنَ ذاكرة المشروع حين يُقبل النداء، لا حين يُعلن."""
    def run(arguments: dict, context) -> dict:
        text = arguments.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ToolRefused("argument_invalid", "الوسيط «text» نصٌّ غير فارغ")
        try:
            item_id = open_store().remember(text, consent="owner", source={"via": "propose_memory"})
        except MemoryRefused as exc:
            raise ToolRefused(exc.code, exc.reason) from None
        return {"content": f"حُفظ في ذاكرة المشروع بموافقة المالك ({item_id}).", "item_id": item_id}
    return Tool(PROPOSE_MEMORY_SPEC, run)
