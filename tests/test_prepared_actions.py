"""Prepared approvals and effects use temporary data, no model or Docker."""
from dataclasses import replace
import base64
import json
import os
from pathlib import Path
import stat

import pytest

from agent.actions import ActionRefused, ActionStore
from agent.journal import Journal
from agent.registry import Tool, ToolContext, ToolRegistry
from core.canonical import digest
from core.contracts import ToolCall, ToolSpec

REQUEST = "a" * 64
POSITION = dict(session_id="session-1", turn_id="turn-1", step_index=0)


def setup(tmp_path, *, consent="owner", handler=None):
    root = tmp_path.resolve() / "work"
    root.mkdir()
    (root / "input.txt").write_text("before")
    state = tmp_path.resolve() / "state"
    store = ActionStore(state, root)
    calls = []
    def run(arguments, context):
        calls.append(arguments)
        (root / "effect.txt").write_text(arguments["value"])
        return {"content": "done"}
    tool = Tool(ToolSpec("fixture", "fixture", {"type": "object"}, consent, consent == "logged"), handler or run)
    registry = ToolRegistry(tool)
    context = ToolContext(root, Journal(root), allowed_consents=frozenset({"auto", "logged"}))
    call = ToolCall("c1", "fixture", {"value": "one"})
    store.register_step(**POSITION, request_digest=REQUEST, calls=(call,), specs=registry.specs())
    return store, registry, context, call, calls


def invoke(store, registry, context, call, **changes):
    kw = {**POSITION, "call_index": 0, "request_digest": REQUEST, **changes}
    return registry.invoke_prepared(call, context, store=store, **kw)


def approve(store, pending):
    return store.decide(pending["action_id"], pending["call_digest"], pending["revision"], approve=True)


def test_approval_is_bound_and_completed_replays_without_handler(tmp_path):
    store, registry, context, call, calls = setup(tmp_path)
    pending = invoke(store, registry, context, call)
    assert pending["status"] == "awaiting_owner" and calls == []
    approve(store, pending)
    result = invoke(store, registry, context, call)
    assert result["status"] == "ok" and len(calls) == 1
    (context.root / "effect.txt").write_text("owner edit")
    reopened = ActionStore(store.directory, context.root)
    assert invoke(reopened, registry, context, call) == result
    assert len(calls) == 1 and (context.root / "effect.txt").read_text() == "owner edit"
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in store.directory.iterdir())


def test_same_call_id_new_arguments_never_inherits_approval(tmp_path):
    store, registry, context, call, calls = setup(tmp_path)
    pending = invoke(store, registry, context, call)
    approve(store, pending)
    changed = replace(call, arguments={"value": "other"})
    assert invoke(store, registry, context, changed)["code"] == "action_binding_conflict"
    assert calls == []
    with pytest.raises(ActionRefused, match="action_binding_conflict"):
        store.register_step(**POSITION, request_digest=REQUEST, calls=(changed,), specs=registry.specs())


@pytest.mark.parametrize("change", ["digest", "revision", "repeat"])
def test_decision_rejects_stale_or_reused_authority(tmp_path, change):
    store, registry, context, call, calls = setup(tmp_path)
    pending = invoke(store, registry, context, call)
    if change == "repeat":
        approved = approve(store, pending)
        with pytest.raises(ActionRefused, match="action_decision_consumed"):
            store.decide(pending["action_id"], pending["call_digest"], approved["revision"], approve=True)
    else:
        with pytest.raises(ActionRefused):
            store.decide(pending["action_id"], "b" * 64 if change == "digest" else pending["call_digest"],
                         pending["revision"] + (change == "revision"), approve=True)
    assert calls == []


def test_decision_has_no_alternative_argument_api(tmp_path):
    store, registry, context, call, _ = setup(tmp_path)
    pending = invoke(store, registry, context, call)
    with pytest.raises(TypeError):
        store.decide(pending["action_id"], pending["call_digest"], pending["revision"],
                     approve=True, arguments={"value": "other"})


def test_denied_action_stays_denied(tmp_path):
    store, registry, context, call, calls = setup(tmp_path)
    pending = invoke(store, registry, context, call)
    store.decide(pending["action_id"], pending["call_digest"], pending["revision"], approve=False)
    assert invoke(store, registry, context, call)["code"] == "action_denied"
    assert calls == []


def test_legacy_approved_ids_never_authorize_owner(tmp_path):
    _, registry, context, call, calls = setup(tmp_path)
    context = replace(context, approved_call_ids=frozenset({call.call_id}))
    assert registry.invoke(call, context)["status"] == "awaiting_owner"
    assert calls == []


def test_changed_workspace_inputs_refuse_without_overwriting_owner(tmp_path):
    store, registry, context, call, calls = setup(tmp_path)
    pending = invoke(store, registry, context, call)
    approve(store, pending)
    (context.root / "input.txt").write_text("owner change")
    assert invoke(store, registry, context, call)["code"] == "action_inputs_changed"
    assert calls == [] and not (context.root / "effect.txt").exists()


def test_crash_after_effect_is_unknown_never_retried(tmp_path):
    class Crash(BaseException):
        pass
    count = []
    holder = {}
    def run(args, ctx):
        count.append(1)
        # The fsynced receipt must already say started at the effect boundary.
        receipt = next(holder["store"].directory.glob("action-*.json"))
        assert json.loads(receipt.read_text())["record"]["state"] == "started"
        (ctx.root / "effect.txt").write_text("landed")
        raise Crash()
    store, registry, context, call, _ = setup(tmp_path, consent="logged", handler=run)
    holder["store"] = store
    with pytest.raises(Crash):
        invoke(store, registry, context, call)
    resumed = ActionStore(store.directory, context.root)
    result = invoke(resumed, registry, context, call)
    assert result["status"] == "outcome_unknown" and count == [1]
    assert resumed.get(result["action_id"])["state"] == "outcome_unknown"


def test_failure_to_persist_completed_is_unknown(tmp_path, monkeypatch):
    store, registry, context, call, calls = setup(tmp_path, consent="logged")
    original = store._save
    def fail(fd, name, value):
        if value.get("state") == "completed":
            raise OSError("synthetic fsync failure")
        return original(fd, name, value)
    monkeypatch.setattr(store, "_save", fail)
    assert invoke(store, registry, context, call)["status"] == "outcome_unknown"
    assert len(calls) == 1
    assert invoke(ActionStore(store.directory, context.root), registry, context, call)["status"] == "outcome_unknown"
    assert len(calls) == 1


def test_started_persistence_failure_prevents_handler(tmp_path, monkeypatch):
    store, registry, context, call, calls = setup(tmp_path, consent="logged")
    original = store._save
    def fail(fd, name, value):
        if value.get("state") == "started":
            raise OSError("synthetic fsync failure")
        return original(fd, name, value)
    monkeypatch.setattr(store, "_save", fail)
    assert invoke(store, registry, context, call)["status"] == "refused"
    assert calls == []


def test_concurrent_cooperating_writer_is_refused_before_effect(tmp_path):
    count, second_results = [], []
    holder = {}
    def run(args, ctx):
        count.append(1)
        second_results.append(invoke(holder["second"], holder["registry"], ctx, holder["call"]))
        return {"content": "done"}
    store, registry, context, call, _ = setup(tmp_path, consent="logged", handler=run)
    holder.update(second=ActionStore(store.directory, context.root), registry=registry, call=call)
    assert invoke(store, registry, context, call)["status"] == "ok"
    assert second_results[0]["code"] == "action_store_busy" and count == [1]


def test_deleted_receipt_cannot_reexecute_registered_action(tmp_path):
    store, registry, context, call, calls = setup(tmp_path, consent="logged")
    result = invoke(store, registry, context, call)
    (store.directory / (result["action_id"] + ".json")).unlink()
    assert invoke(store, registry, context, call)["code"] == "action_receipt_missing"
    assert len(calls) == 1


def test_old_replayed_model_response_without_step_receipt_is_rejected(tmp_path):
    store, registry, context, call, _ = setup(tmp_path)
    with pytest.raises(ActionRefused, match="action_receipt_missing"):
        store.register_step(**{**POSITION, "step_index": 1}, request_digest=REQUEST,
                            calls=(call,), specs=registry.specs(), allow_new=False)
    store.register_step(**POSITION, request_digest=REQUEST, calls=(call,), specs=registry.specs(), allow_new=False)


def test_later_call_inputs_are_prepared_after_earlier_effect(tmp_path):
    store, registry, context, call, calls = setup(tmp_path, consent="logged")
    # A separate step has two calls; only the first has occurred before resumption.
    second = replace(call, call_id="c2", arguments={"value": "two"})
    position = {**POSITION, "step_index": 1}
    store.register_step(**position, request_digest=REQUEST, calls=(call, second), specs=registry.specs())
    first = invoke(store, registry, context, call, step_index=1)
    store.register_step(**position, request_digest=REQUEST, calls=(call, second), specs=registry.specs(), allow_new=False)
    assert invoke(store, registry, context, call, step_index=1) == first
    assert invoke(store, registry, context, second, step_index=1, call_index=1)["status"] == "ok"
    assert calls == [{"value": "one"}, {"value": "two"}]


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_receipt_link_never_reads_or_overwrites_outside_file(tmp_path, kind):
    store, registry, context, call, calls = setup(tmp_path)
    pending = invoke(store, registry, context, call)
    receipt = store.directory / (pending["action_id"] + ".json")
    outside = tmp_path / "outside.json"
    outside.write_bytes(receipt.read_bytes())
    outside.chmod(0o600)
    before = outside.read_bytes()
    receipt.unlink()
    if kind == "symlink":
        receipt.symlink_to(outside)
    else:
        os.link(outside, receipt)
    assert invoke(store, registry, context, call)["code"] == "action_state_unsafe"
    assert outside.read_bytes() == before and calls == []


def test_private_state_must_be_outside_workspace_and_no_symlink_roots(tmp_path):
    root = tmp_path.resolve() / "work"
    root.mkdir()
    with pytest.raises(ActionRefused, match="action_state_inside_workspace"):
        ActionStore(root / ".private", root)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ActionRefused, match="action_state_unsafe"):
        ActionStore(tmp_path.resolve() / "state", alias)


def test_workspace_replacement_invalidates_existing_approval(tmp_path):
    store, registry, context, call, calls = setup(tmp_path)
    pending = invoke(store, registry, context, call)
    approve(store, pending)
    context.root.rename(tmp_path / "old-work")
    context.root.mkdir()
    assert invoke(store, registry, context, call)["code"] == "action_workspace_changed"
    assert calls == []


def test_journal_for_different_workspace_cannot_escape_bound_action(tmp_path):
    store, registry, context, call, calls = setup(tmp_path, consent="logged")
    other = tmp_path.resolve() / "other"
    other.mkdir()
    changed = replace(context, journal=Journal(other))
    assert invoke(store, registry, changed, call)["code"] == "action_workspace_changed"
    assert calls == []


def test_snapshot_rejects_symlinks_and_hardlinks(tmp_path):
    store, registry, context, call, calls = setup(tmp_path)
    os.link(context.root / "input.txt", context.root / "alias.txt")
    assert invoke(store, registry, context, call)["code"] == "action_state_unsafe"
    assert calls == []


def test_journal_identifier_is_distinct_from_action_receipt(tmp_path):
    store, registry, context, call, _ = setup(tmp_path, consent="logged",
        handler=lambda args, ctx: {"content": "ok", "action_id": "act-fixture"})
    result = invoke(store, registry, context, call)
    assert result["action_id"].startswith("action-") and result["journal_action_id"] == "act-fixture"
    assert store.get(result["action_id"])["reversible"] is True
    saved = store.completed_result(result["action_id"])
    assert saved == result
    saved["journal_action_id"] = "mutated-by-caller"
    assert store.completed_result(result["action_id"])["journal_action_id"] == "act-fixture"


def test_completed_result_refuses_pending_approval(tmp_path):
    store, registry, context, call, _ = setup(tmp_path)
    pending = invoke(store, registry, context, call)
    with pytest.raises(ActionRefused, match="action_not_completed"):
        store.completed_result(pending["action_id"])


def test_container_consumes_approved_bytes_without_docker(tmp_path, monkeypatch):
    from core import execution
    from agent.builtin_tools import RUN_COMMAND
    root = tmp_path.resolve() / "work"
    root.mkdir()
    source = root / "script.py"
    source.write_text("original")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"schema_version": 1, "image_id": "sha256:" + "a" * 64,
                                  "lock_sha256": "b" * 64, "python_version": "3.12.1"}))
    receipt.chmod(0o600)
    backend = execution.DockerExecutionBackend(receipt.resolve(), root, ("script.py",))
    monkeypatch.setattr(execution, "_BACKENDS", {root: backend})
    store, registry = ActionStore(tmp_path.resolve() / "state", root), ToolRegistry(RUN_COMMAND)
    context = ToolContext(root, Journal(root))
    call = ToolCall("c1", "run_command", {"argv": ["python", "script.py"]})
    store.register_step(**POSITION, request_digest=REQUEST, calls=(call,), specs=registry.specs())
    pending = invoke(store, registry, context, call)
    assert pending["status"] == "awaiting_owner"
    approve(store, pending)
    source.write_text("owner edited after approval")
    seen = []
    monkeypatch.setattr(backend, "_verify_image", lambda: None)
    def run(driver, payload, *, timeout_s):
        seen.append(payload)
        return execution.ExecutionResult(0, "ok", "", "synthetic-container")
    monkeypatch.setattr(backend, "_run_container", run)
    result = invoke(store, registry, context, call)
    assert result["status"] == "ok"
    assert base64.b64decode(seen[-1]["files"][0]["data"]) == b"original"
    assert invoke(store, registry, context, call) == result and len(seen) == 2
    assert execution._FROZEN_INPUTS.get() is None


def test_trusted_selector_captures_new_previous_step_file_once(tmp_path, monkeypatch):
    from core import execution
    root = tmp_path.resolve() / "work"
    root.mkdir()
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"schema_version": 1, "image_id": "sha256:" + "a" * 64,
                                  "lock_sha256": "b" * 64, "python_version": "3.12.1"}))
    receipt.chmod(0o600)
    selected_roots = []
    def selector(selected_root):
        selected_roots.append(selected_root)
        return tuple(sorted(p.name for p in selected_root.iterdir() if p.is_file()))
    monkeypatch.setattr(execution, "_BACKENDS", {})
    backend = execution.configure_execution_backend(receipt.resolve(), root, snapshot_selector=selector)
    # The file did not exist at bootstrap; a prior agent step wrote it.
    Journal(root).write_file("new.py", "approved program")
    frozen = execution.freeze_execution_inputs(root)
    assert frozen["approved_paths"] == ["new.py"] and selected_roots == [root]
    (root / "new.py").write_text("later edit")
    (root / "later.py").write_text("unapproved later program")
    payloads = []
    monkeypatch.setattr(backend, "_verify_image", lambda: None)
    def run(driver, payload, *, timeout_s):
        payloads.append(payload)
        return execution.ExecutionResult(0, "ok", "", "fixture")
    monkeypatch.setattr(backend, "_run_container", run)
    with execution.frozen_execution_inputs(root, frozen):
        execution.execute_candidate(("python", "new.py"), root)
    assert selected_roots == [root]
    assert [f["path"] for f in payloads[-1]["files"]] == ["new.py"]
    assert base64.b64decode(payloads[-1]["files"][0]["data"]) == b"approved program"


@pytest.mark.parametrize("mutation", ["receipt", "selected_name", "bytes", "oversized"])
def test_frozen_inputs_refuse_corrupt_or_changed_configuration(tmp_path, monkeypatch, mutation):
    from core import execution
    root = tmp_path.resolve() / "work"
    root.mkdir()
    (root / "file.py").write_text("x")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"schema_version": 1, "image_id": "sha256:" + "a" * 64,
                                  "lock_sha256": "b" * 64, "python_version": "3.12.1"}))
    receipt.chmod(0o600)
    backend = execution.DockerExecutionBackend(receipt.resolve(), root, ("file.py",))
    monkeypatch.setattr(execution, "_BACKENDS", {root: backend})
    frozen = execution.freeze_execution_inputs(root)
    if mutation == "receipt":
        backend.receipt["image_id"] = "sha256:" + "c" * 64
    elif mutation == "selected_name":
        frozen["approved_paths"] = ["../escape"]
    elif mutation == "bytes":
        frozen["files"][0]["data"] = "!badbase64"
    else:
        monkeypatch.setattr(execution, "MAX_FILE_BYTES", 0)
    with pytest.raises(execution.ExecutionRefused):
        with execution.frozen_execution_inputs(root, frozen):
            pytest.fail("invalid inputs must fail before any call")
    assert execution._FROZEN_INPUTS.get() is None
