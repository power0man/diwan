"""Owner-requested Journal reversal using the same durable action receipts.

This helper is a control API, never a model tool. Calling it is the explicit
owner request to approve this particular reversal. request_id binds that request
once; a started reversal without completion is unknown and is not repeated.
Reversing a reversal is not promised: the reversal declares reversible=False.
"""
from __future__ import annotations

from agent.actions import ActionRefused, ActionStore, IDENTIFIER
from agent.journal import Journal, JournalRefused
from agent.registry import Tool, ToolContext, ToolRegistry
from core.canonical import digest
from core.contracts import ToolCall, ToolSpec


def revert_prepared(store: ActionStore, context: ToolContext, source_action_id: str, *,
                    session_id: str, request_id: str) -> dict:
    if (not isinstance(store, ActionStore) or not isinstance(context, ToolContext)
            or not isinstance(context.journal, Journal)
            or context.root != store.workspace or context.journal.root != store.workspace):
        raise ActionRefused("action_workspace_changed", "سياق الرجوع لا يطابق دفتر مساحة الفعل")
    if any(not isinstance(value, str) or not IDENTIFIER.fullmatch(value)
           for value in (session_id, request_id)):
        raise ActionRefused("action_identity_invalid", "هوية جلسة وطلب رجوع صريحان مطلوبان")
    # Read before invoking the handler: the handler executes with store.lock held.
    source = store.get(source_action_id)
    saved = store.completed_result(source_action_id)
    if source["position"]["session_id"] != session_id:
        raise ActionRefused("action_session_mismatch", "الفعل لا يتبع جلسة طلب الرجوع")
    journal_id = saved.get("journal_action_id")
    if (not source["reversible"] or saved.get("status") != "ok"
            or not isinstance(journal_id, str)):
        raise ActionRefused("action_not_reversible", "لا إيصال رجوع مثبت لهذا الفعل")
    try:
        context.journal.action(journal_id)  # Verify the linked Journal record exists.
    except JournalRefused as exc:
        raise ActionRefused(exc.code, exc.reason) from exc
    binding = {"control": "journal_revert_v1", "session_id": session_id,
               "request_id": request_id, "source_action_id": source_action_id,
               "source_call_digest": source["call_digest"], "journal_action_id": journal_id}
    request_digest = digest(binding)
    turn_id = "revert-" + digest({"request_id": request_id})
    call = ToolCall("revert-" + request_digest[:40], "revert_prepared_action",
                    {"source_action_id": source_action_id, "journal_action_id": journal_id})

    def handler(arguments, tool_context):
        result = tool_context.journal.revert(arguments["journal_action_id"])
        # Keep Journal's semantic result under content. Its status is distinct
        # from the registry's ok/refused execution status.
        return {"content": result, "action_id": journal_id}

    spec = ToolSpec("revert_prepared_action", "Owner-requested Journal reversal", {},
                    consent="owner", reversible=False)
    registry = ToolRegistry(Tool(spec, handler))
    position = dict(session_id=session_id, turn_id=turn_id, step_index=0,
                    request_digest=request_digest)
    store.register_step(**position, calls=(call,), specs=(spec,))
    result = registry.invoke_prepared(call, context, store=store, **position, call_index=0)
    if result["status"] == "awaiting_owner":
        # This API itself is an explicit owner control request, not model text.
        store.decide(result["action_id"], result["call_digest"], result["revision"], approve=True)
        result = registry.invoke_prepared(call, context, store=store, **position, call_index=0)
    return result
