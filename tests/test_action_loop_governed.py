"""Legacy adapter uses the shared action store; no raw approval or rollback."""
from dataclasses import replace
import json

import pytest

from agent.actions import ActionStore
from agent.journal import Journal
from agent.registry import ToolContext
from core.action_loop import ActionControl, GovernedActionLoop
from core.contracts import ToolCall, ToolSpec
from core.ledger import Ledger
from core.tools_registry import create_default_action_loop


@pytest.fixture
def control(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    context = ToolContext(root, Journal(root), frozenset({"auto", "logged"}))
    return ActionControl(ActionStore(tmp_path / "control", root), context, "session", "turn")


def test_auto_tool_keeps_structured_output_without_claiming_durable_replay():
    spec = ToolSpec("lookup_term", "lookup", {}, consent="auto")
    called = []
    def handler(args, ctx):
        called.append(1)
        return {"definition": args["term"]}
    loop = GovernedActionLoop({spec.name: spec}, {spec.name: handler})
    call = ToolCall("c1", spec.name, {"term": "صابورة"})
    result = loop.dispatch_call(call)
    assert result.success and result.consent_grade == "auto"
    assert result.output == {"definition": "صابورة"}
    assert not result.reversible and result.action_id is None
    assert loop.dispatch_call(call, replayed=True).error == "action_store_required"
    assert called == [1]


@pytest.mark.parametrize("grade", ["logged", "owner"])
@pytest.mark.parametrize("old_approval", [False, True])
def test_legacy_context_or_approval_cannot_authorize_an_effect(grade, old_approval, tmp_path):
    effects = []
    spec = ToolSpec("effect", "fixture", {}, consent=grade, reversible=grade == "logged")
    loop = GovernedActionLoop({spec.name: spec}, {spec.name: lambda *a: effects.append(1)},
                              ledger=Ledger(tmp_path / "legacy.jsonl"))
    result = loop.dispatch_call(ToolCall("c1", spec.name, {}),
        context={"workspace_root": tmp_path, "snapshot_fn": lambda: effects.append("snapshot")},
        owner_approved=old_approval)
    assert result.error == "action_control_required"
    assert not result.success and not result.pending_owner
    assert effects == [] and loop.ledger.entries() == []


def test_logged_effect_uses_durable_receipt_and_journal_rollback(control):
    target = control.context.root / "note.txt"
    target.write_text("before")
    loop = create_default_action_loop()
    call = ToolCall("c1", "write_workspace_document", {"path": "note.txt", "content": "after"})
    result = loop.dispatch_call(call, control=control)
    assert result.success and result.reversible
    assert result.action_id.startswith("action-")
    assert result.output["action_id"] == result.journal_action_id
    assert control.store.completed_result(result.action_id)["compat_output"] == result.output
    assert target.read_text() == "after"
    callback_calls = []
    assert loop.rollback(result, lambda *a: callback_calls.append(1), control=control, request_id="undo-1")
    assert target.read_text() == "before" and callback_calls == []
    target.write_text("owner edit after revert")
    assert loop.rollback(result, control=control, request_id="undo-1")
    assert target.read_text() == "owner edit after revert"


def test_rollback_never_uses_a_raw_callback_or_erases_later_owner_edit(control):
    target = control.context.root / "note.txt"
    target.write_text("before")
    loop = create_default_action_loop()
    result = loop.dispatch_call(ToolCall("c1", "write_workspace_document",
                               {"path": "note.txt", "content": "after"}), control=control)
    invoked = []
    assert not loop.rollback(result, lambda *a: invoked.append(1))
    target.write_text("owner edit")
    assert not loop.rollback(result, lambda *a: invoked.append(1), control=control, request_id="undo-1")
    assert target.read_text() == "owner edit" and invoked == []


def test_owner_approval_is_single_use_and_bound_to_the_exact_action(control):
    effects = []
    spec = ToolSpec("owner_effect", "fixture", {}, consent="owner")
    def handler(args, ctx):
        effects.append(dict(args))
        (ctx.root / "owner.txt").write_text(args["value"])
        return {"done": True}
    loop = GovernedActionLoop({spec.name: spec}, {spec.name: handler})
    call = ToolCall("c1", spec.name, {"value": "one"})
    pending = loop.dispatch_call(call, control=control, owner_approved=True)
    assert pending.pending_owner and pending.error == "consent_required"
    assert pending.consent_grade == "owner" and effects == []
    control.store.decide(pending.action_id, pending.call_digest, pending.revision, approve=True)
    changed = loop.dispatch_call(replace(call, arguments={"value": "other"}), control=control)
    assert changed.error == "action_binding_conflict" and effects == []
    completed = loop.dispatch_call(call, control=control, replayed=True)
    assert completed.success and completed.consent_grade == "owner"
    assert loop.dispatch_call(call, control=control, replayed=True) == completed
    assert effects == [{"value": "one"}]


def test_context_grants_are_shared_and_legacy_approved_ids_are_inert(control):
    control = replace(control, context=replace(control.context, allowed_consents=frozenset({"auto"}),
                                               approved_call_ids=frozenset({"c1"})))
    result = create_default_action_loop().dispatch_call(ToolCall("c1", "write_workspace_document",
        {"path": "note.txt", "content": "after"}), control=control, owner_approved=True)
    assert result.pending_owner
    assert not (control.context.root / "note.txt").exists()


def test_crash_after_file_effect_leaves_a_shared_unknown_receipt(control):
    class Crash(BaseException):
        pass
    calls = []
    def handler(args, ctx):
        calls.append(1)
        receipt = next(control.store.directory.glob("action-*.json"))
        assert json.loads(receipt.read_text())["record"]["state"] == "started"
        (ctx.root / "effect.txt").write_text("landed")
        raise Crash()
    spec = ToolSpec("crash_effect", "fixture", {}, consent="logged", reversible=True)
    loop = GovernedActionLoop({spec.name: spec}, {spec.name: handler})
    call = ToolCall("c1", spec.name, {})
    with pytest.raises(Crash):
        loop.dispatch_call(call, control=control)
    result = loop.dispatch_call(call, control=control, replayed=True)
    assert result.status == "outcome_unknown"
    assert result.error == "action_outcome_unknown" and calls == [1]
    assert (control.context.root / "effect.txt").read_text() == "landed"


def test_unsafe_path_and_unregistered_tool_cannot_produce_effects(control):
    loop = create_default_action_loop()
    result = loop.dispatch_call(ToolCall("c1", "write_workspace_document",
        {"path": "../escaped.txt", "content": "no"}), control=control)
    assert not result.success and result.error == "path_invalid"
    assert not (control.context.root.parent / "escaped.txt").exists()
    unknown = loop.dispatch_call(ToolCall("c2", "unregistered_tool", {}))
    assert not unknown.success and "غير مسجلة" in unknown.error


def test_dual_context_is_refused_before_effect(control):
    result = create_default_action_loop().dispatch_call(ToolCall("c1", "write_workspace_document",
        {"path": "note.txt", "content": "no"}), control=control,
        context={"workspace_root": control.context.root.parent})
    assert result.error == "action_context_conflict"
    assert not (control.context.root / "note.txt").exists()


def test_historical_replay_without_action_plan_is_refused(control):
    result = create_default_action_loop().dispatch_call(ToolCall("c1", "write_workspace_document",
        {"path": "note.txt", "content": "no"}), control=control, replayed=True)
    assert result.error == "action_receipt_missing"
    assert not (control.context.root / "note.txt").exists()


def test_revert_crash_after_effect_never_repeats_with_same_request(control, monkeypatch):
    from agent.action_revert import revert_prepared
    class Crash(BaseException):
        pass
    target = control.context.root / "note.txt"
    target.write_text("before")
    loop = create_default_action_loop()
    source = loop.dispatch_call(ToolCall("c1", "write_workspace_document",
        {"path": "note.txt", "content": "after"}), control=control)
    original = control.context.journal.revert
    calls = []
    def crash_after_revert(action_id):
        calls.append(action_id)
        original(action_id)
        raise Crash()
    monkeypatch.setattr(control.context.journal, "revert", crash_after_revert)
    with pytest.raises(Crash):
        revert_prepared(control.store, control.context, source.action_id,
                        session_id=control.session_id, request_id="undo-once")
    assert target.read_text() == "before"
    target.write_text("later owner change")
    reopened = ActionStore(control.store.directory, control.context.root)
    resumed = revert_prepared(reopened, control.context, source.action_id,
                              session_id=control.session_id, request_id="undo-once")
    assert resumed["status"] == "outcome_unknown"
    assert calls == [source.journal_action_id]
    assert target.read_text() == "later owner change"
    assert not reopened.get(resumed["action_id"])["reversible"]


def test_revert_request_cannot_bind_another_source_or_session(control):
    from agent.action_revert import revert_prepared
    from agent.actions import ActionRefused
    loop = create_default_action_loop()
    first = loop.dispatch_call(ToolCall("c1", "write_workspace_document",
        {"path": "one.txt", "content": "one"}), control=control)
    second = loop.dispatch_call(ToolCall("c2", "write_workspace_document",
        {"path": "two.txt", "content": "two"}), control=replace(control, turn_id="second"))
    with pytest.raises(ActionRefused, match="action_session_mismatch"):
        revert_prepared(control.store, control.context, first.action_id,
                        session_id="another-session", request_id="undo")
    assert revert_prepared(control.store, control.context, first.action_id,
                           session_id=control.session_id, request_id="undo")["status"] == "ok"
    with pytest.raises(ActionRefused, match="action_binding_conflict"):
        revert_prepared(control.store, control.context, second.action_id,
                        session_id=control.session_id, request_id="undo")
    assert (control.context.root / "two.txt").read_text() == "two"


def test_revert_uses_saved_journal_identity_not_mutable_result_fields(control):
    loop = create_default_action_loop()
    first = loop.dispatch_call(ToolCall("c1", "write_workspace_document",
        {"path": "one.txt", "content": "one"}), control=control)
    second = loop.dispatch_call(ToolCall("c2", "write_workspace_document",
        {"path": "two.txt", "content": "two"}), control=replace(control, turn_id="second"))
    forged = replace(first, journal_action_id=second.journal_action_id,
                     output={"action_id": second.journal_action_id})
    assert loop.rollback(forged, control=control, request_id="undo")
    assert not (control.context.root / "one.txt").exists()
    assert (control.context.root / "two.txt").read_text() == "two"
