"""Compatibility adapter over the shared registry and durable ActionStore.

Effectful callers supply ActionControl explicitly. Legacy owner_approved,
snapshot_fn and raw rollback callbacks confer no authority. Pure declared auto
handlers can still run without control, as reads with no durable replay promise.
The old ledger parameter remains accepted; receipts are the effect record and
create no second signing or permission path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from types import SimpleNamespace

from agent.actions import ActionStore
from agent.registry import Tool, ToolContext, ToolRegistry
from core.canonical import PayloadRejected, check_payload, digest
from core.contracts import CALL_ID, CONSENT_GRADES, ToolCall, ToolSpec
from core.ledger import Ledger


class ActionConsentRequired(RuntimeError):
    """Legacy exception name retained for import compatibility."""
    def __init__(self, tool_name: str, call_id: str):
        super().__init__(f"الأداة {tool_name!r} تنتظر قرارًا مربوطًا بإيصال [call_id={call_id}]")
        self.tool_name = tool_name
        self.call_id = call_id


@dataclass(frozen=True)
class ActionControl:
    store: ActionStore
    context: ToolContext
    session_id: str
    turn_id: str
    step_index: int = 0


@dataclass(frozen=True)
class ActionResult:
    call_id: str
    name: str
    success: bool
    output: Any
    consent_grade: str
    reversible: bool
    error: str | None = None
    pending_owner: bool = False
    snapshot: dict | None = None
    # Trailing fields preserve the positional legacy result API.
    status: str = "refused"
    action_id: str | None = None
    call_digest: str | None = None
    revision: int | None = None
    journal_action_id: str | None = None

    def payload(self) -> dict:
        return {
            "call_id": self.call_id, "name": self.name, "success": self.success,
            "output": self.output, "consent_grade": self.consent_grade,
            "reversible": self.reversible, "error": self.error,
            "pending_owner": self.pending_owner, "status": self.status,
            "action_id": self.action_id, "call_digest": self.call_digest,
            "revision": self.revision, "journal_action_id": self.journal_action_id,
        }


class GovernedActionLoop:
    """Old dispatch shape, with shared permissions and effect receipts."""

    def __init__(self, tools: dict[str, ToolSpec],
                 handlers: dict[str, Callable[[dict, Any], Any]],
                 ledger: Ledger | None = None, max_steps: int = 10):
        self.tools, self.handlers = dict(tools), dict(handlers)
        self.ledger, self.max_steps = ledger, max_steps

    def _registry(self, context):
        tools = []
        for name, spec in self.tools.items():
            if name not in self.handlers:
                continue
            handler = self.handlers[name]

            def runner(arguments, _context, handler=handler):
                output = handler(arguments, context)
                result = {"content": "", "compat_output": output}
                # The shared store moves this ID to journal_action_id. Preserve
                # the handler output as well for legacy readers.
                if isinstance(output, dict) and isinstance(output.get("action_id"), str):
                    result["action_id"] = output["action_id"]
                return result

            tools.append(Tool(spec, runner))
        return ToolRegistry(*tools)

    @staticmethod
    def _refused(call, spec, code):
        return ActionResult(call.call_id, call.name, False, None,
                            spec.consent if spec else "unknown", False, code)

    def dispatch_call(self, call: ToolCall, context: dict | None = None,
                      owner_approved: bool = False,
                      now_ts: str = "2026-09-22T00:00:00+00:00", *,
                      control: ActionControl | None = None,
                      replayed: bool = False) -> ActionResult:
        """Dispatch one declared call; old boolean approvals are inert."""
        if (not isinstance(call, ToolCall) or not isinstance(call.name, str)
                or not isinstance(call.call_id, str) or not CALL_ID.fullmatch(call.call_id)
                or not isinstance(call.arguments, dict)):
            return self._refused(ToolCall("invalid", "invalid", {}), None, "tool_call_invalid")
        spec = self.tools.get(call.name)
        if spec is None or call.name not in self.handlers:
            return self._refused(call, None, "tool_unknown: أداة غير مسجلة")
        if (not isinstance(spec, ToolSpec) or spec.name != call.name
                or spec.consent not in CONSENT_GRADES or type(spec.reversible) is not bool
                or (spec.consent == "logged" and not spec.reversible)):
            return self._refused(call, None, "tool_spec_invalid")
        try:
            check_payload(call.declared())
            check_payload(spec.declared())
            if control is None:
                if spec.consent != "auto":
                    return self._refused(call, spec, "action_control_required")
                if replayed:
                    return self._refused(call, spec, "action_store_required")
                # A permission-only shim has no workspace or journal. The legacy
                # context reaches only a handler declared to be a pure read.
                registry = self._registry(context)
                result = registry.invoke(call, SimpleNamespace(allowed_consents=frozenset({"auto"})))
            else:
                if not isinstance(control, ActionControl) or not isinstance(control.store, ActionStore):
                    return self._refused(call, spec, "action_control_invalid")
                if not isinstance(control.context, ToolContext):
                    return self._refused(call, spec, "action_context_required")
                if context is not None:
                    return self._refused(call, spec, "action_context_conflict")
                registry = self._registry(control.context)
                request_digest = digest({"adapter": "governed_action_v1",
                                         "call": call.declared(), "spec": spec.declared()})
                position = dict(session_id=control.session_id, turn_id=control.turn_id,
                                step_index=control.step_index, request_digest=request_digest)
                control.store.register_step(**position, calls=(call,), specs=registry.specs(),
                                            allow_new=not replayed)
                result = registry.invoke_prepared(call, control.context, store=control.store,
                                                  **position, call_index=0, allow_new=True)
        except PayloadRejected as exc:
            return self._refused(call, spec, exc.code)
        status = result["status"]
        return ActionResult(
            call.call_id, call.name, status == "ok", result.get("compat_output"),
            spec.consent, bool(spec.reversible and result.get("journal_action_id")),
            result.get("code"), status == "awaiting_owner", None, status,
            result.get("action_id"), result.get("call_digest"), result.get("revision"),
            result.get("journal_action_id"),
        )

    def rollback(self, action_result: ActionResult, revert_fn: Callable | None = None, *,
                 control: ActionControl | None = None, request_id: str | None = None) -> bool:
        """A legacy callback never authorizes an effect or overrides owner edits."""
        if (not isinstance(control, ActionControl) or not request_id
                or not action_result.action_id):
            return False
        from agent.action_revert import revert_prepared
        try:
            result = revert_prepared(control.store, control.context, action_result.action_id,
                                     session_id=control.session_id, request_id=request_id)
        except PayloadRejected:
            return False
        return result.get("status") == "ok"
