"""سجلُّ الأدوات ومنفّذها — الإذنُ درجةٌ مُعلَنة، لا محرّكُ سياسات.

القاعدةُ كلُّها سطران:

  * **الميثاق** يمنح *درجات* (`auto`/`logged`)، مرّةً واحدة سلفًا. وما دام
    النداءُ داخلها لا يُسأل المالك واقعةً بواقعة.
  * **الموافقة** تمنح فعلًا مثبت الحجج واللقطة في ActionStore. لا يكفي
    `call_id` منفردًا، ودرجةُ `owner` لا تُمنح ميثاقًا أبدًا.

وما تعجز عنه الأداةُ يُردّ برمزٍ مسمّى ويعود إلى النموذج نصًّا: فالحلقةُ
تتعلّم من الردّ كما تتعلّم من النجاح. ولا يُسقَط ردٌّ صمتًا.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from pathlib import Path

from core.canonical import PayloadRejected
from core.contracts import ToolCall, ToolSpec
from agent.journal import Journal, JournalRefused
from workspace_tools.files import WorkspaceError

MAX_RESULT_BYTES = 16_384
TRUNCATED = "\n…[قُطع الناتج عند الحدّ المعلن]"


class ToolRefused(RuntimeError):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{reason} [{code}]")
        self.code = code
        self.reason = reason


# أخطاءٌ تحمل رمزًا وسببًا: كلُّها رفضٌ مسمّى لا عطب
_NAMED_REFUSALS = (ToolRefused, JournalRefused, WorkspaceError, PayloadRejected)


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    run: Callable[[dict, "ToolContext"], dict]


@dataclass(frozen=True)
class ToolContext:
    root: Path
    journal: Journal
    allowed_consents: frozenset = frozenset({"auto"})
    approved_call_ids: frozenset = frozenset()
    disposable_host: str | None = None

    def __post_init__(self):
        if "owner" in self.allowed_consents:
            raise ValueError("درجةُ المالك لا تُمنح ميثاقًا — تُمنح نداءً بعينه")


class ToolRegistry:
    def __init__(self, *tools: Tool):
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.spec.name in self._tools:
                raise ValueError(f"أداةٌ مكرَّرة: {tool.spec.name}")
            self._tools[tool.spec.name] = tool

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools[name].spec for name in sorted(self._tools))

    def invoke(self, call: ToolCall, context: ToolContext) -> dict:
        base = {"call_id": call.call_id, "name": call.name}
        tool = self._tools.get(call.name)
        if tool is None:
            # لا يقع عادةً: النواةُ ترفض أداةً غير مُعلَنة قبل بلوغ هذا
            # الموضع. ويبقى الفحصُ لأن المنفّذ قد يُستدعى من غير حلقة.
            return {**base, "status": "refused", "code": "tool_unknown",
                    "content": f"لا أداةَ بهذا الاسم: {call.name}"}
        # Legacy call IDs remain accepted as constructor data only. They cannot
        # authorize new arguments, or bypass a durable prepared-action decision.
        permitted = tool.spec.consent in context.allowed_consents
        if not permitted:
            return {**base, "status": "awaiting_owner", "code": "consent_required",
                    "consent": tool.spec.consent,
                    "content": f"هذا النداء بدرجة «{tool.spec.consent}» وينتظر إذنك"}
        return self._run(call, context, tool)

    def invoke_prepared(self, call: ToolCall, context: ToolContext, *, store,
                        session_id: str, turn_id: str, step_index: int,
                        call_index: int, request_digest: str, allow_new: bool = True) -> dict:
        """Use the stored step plan, immutable approval and durable effect receipt."""
        from agent.actions import ActionRefused, ActionStore
        from core.canonical import canonical_bytes
        base = {"call_id": call.call_id, "name": call.name}
        tool = self._tools.get(call.name)
        if tool is None:
            return {**base, "status": "refused", "code": "tool_unknown", "content": "أداة غير معلنة"}
        if not isinstance(store, ActionStore):
            return {**base, "status": "refused", "code": "action_store_required", "content": "مخزن أفعال مطلوب"}
        try:
            frozen = ToolCall(call.call_id, call.name, json.loads(canonical_bytes(call.arguments)))
            return store.invoke(frozen, tool.spec, context, lambda: self._run(frozen, context, tool),
                session_id=session_id, turn_id=turn_id, step_index=step_index,
                call_index=call_index, request_digest=request_digest, allow_new=allow_new)
        except (ActionRefused, *_NAMED_REFUSALS) as exc:
            return {**base, "status": "refused", "code": exc.code, "content": exc.reason}

    def _run(self, call, context, tool):
        base = {"call_id": call.call_id, "name": call.name}
        try:
            result = tool.run(dict(call.arguments), context)
        except _NAMED_REFUSALS as exc:
            # رفضٌ مسمّى ≠ عطب. والفرقُ حاملٌ للحلقة: الرفضُ يعود إلى
            # النموذج ليصحّح نداءه، والعطبُ إشارةُ خللٍ في النظام نفسه.
            return {**base, "status": "refused", "code": exc.code, "content": exc.reason}
        except Exception as exc:                      # عطبٌ غير متوقَّع يُسمَّى ولا يُخفى
            return {**base, "status": "failed", "code": "tool_raised",
                    "content": f"{type(exc).__name__}: {str(exc)[:400]}"}
        content = result.pop("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, sort_keys=True)
        raw = content.encode("utf-8")
        if len(raw) > MAX_RESULT_BYTES:
            content = raw[:MAX_RESULT_BYTES].decode("utf-8", "ignore") + TRUNCATED
        return {**base, "status": "ok", "content": content, **result}
