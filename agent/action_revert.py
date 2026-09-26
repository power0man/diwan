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


MAX_GROUP = 16


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
    # A call that wrote several files (analyze_data) is reverted as one group.
    group = saved.get("journal_action_ids")
    if not source["reversible"] or saved.get("status") != "ok":
        raise ActionRefused("action_not_reversible", "لا إيصال رجوع مثبت لهذا الفعل")
    if group is None:
        if not isinstance(journal_id, str):
            raise ActionRefused("action_not_reversible", "لا إيصال رجوع مثبت لهذا الفعل")
        linked = [journal_id]
    elif (journal_id is not None or not isinstance(group, list) or not 1 <= len(group) <= MAX_GROUP
          or not all(isinstance(item, str) for item in group) or len(set(group)) != len(group)):
        raise ActionRefused("action_not_reversible", "مجموعةُ إيصالات الرجوع غير صالحة")
    else:
        linked = list(group)
    try:
        for item in linked:
            context.journal.action(item)  # Verify every linked Journal record exists.
    except JournalRefused as exc:
        raise ActionRefused(exc.code, exc.reason) from exc
    if group is None:
        control, link = "journal_revert_v1", {"journal_action_id": journal_id}
    else:
        control, link = "journal_revert_group_v1", {"journal_action_ids": linked}
    binding = {"control": control, "session_id": session_id,
               "request_id": request_id, "source_action_id": source_action_id,
               "source_call_digest": source["call_digest"], **link}
    request_digest = digest(binding)
    turn_id = "revert-" + digest({"request_id": request_id})
    call = ToolCall("revert-" + request_digest[:40], "revert_prepared_action",
                    {"source_action_id": source_action_id, **link})

    def handler(arguments, tool_context):
        if group is None:
            result = tool_context.journal.revert(arguments["journal_action_id"])
            # Keep Journal's semantic result under content. Its status is distinct
            # from the registry's ok/refused execution status.
            return {"content": result, "action_id": journal_id}
        # Every file is checked before any is reverted: a later owner edit to one
        # output refuses the whole group instead of leaving it half reverted.
        for item in arguments["journal_action_ids"]:
            tool_context.journal.check_revert(item)
        results = [tool_context.journal.revert(item) for item in reversed(arguments["journal_action_ids"])]
        return {"content": results, "action_ids": linked}

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
