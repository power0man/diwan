"""Synthetic local protocol evidence only; no cloud network or live model."""
from contextlib import contextmanager
import base64
import copy
import json
import multiprocessing
from pathlib import Path
import shutil
import uuid

import pytest

from core.canonical import canonical_bytes, digest
from memory.store import MemoryStore
from tests.test_cloud_checkpoints import TestStore
from tests.test_cloud_workspace_scope import _app, _call, _create, Provider, TARGET
from workspace_tools import backup, checkpoints as cp, storage_scope as ss
from workspace_tools import synthetic_checkpoints as sc


class BoundStore(TestStore):
    """In-memory contract double, not evidence of a remote durable lease."""
    def __init__(self, state=None):
        super().__init__(state)
        self.verified = False
        self.bad_identity = False

    @contextmanager
    def exclusive(self, *, require_bound=False):
        if require_bound and "scope" not in self.state:
            raise OSError("synthetic scope unbound")
        self.calls.append("require-bound" if require_bound else "allow-bind")
        self.verified = False
        with super().exclusive():
            yield
        self.verified = False

    def bind_scope(self, raw):
        assert self.locked
        self.calls.append("bind")
        if "scope" not in self.state:
            assert self.is_pristine()
            self.state["scope"] = raw
        return self.verify_scope(raw)

    def verify_scope(self, raw):
        assert self.locked
        self.calls.append("verify")
        if self.state.get("scope") != raw:
            raise OSError("synthetic mismatching scope")
        self.verified = True
        value = ss.checkpoint_scope(raw)
        return {**value, "workspace_id": "0" * 32} if self.bad_identity else value

    def read_head(self):
        assert self.verified
        return super().read_head()

    def get(self, key):
        assert self.verified
        return super().get(key)


@pytest.fixture
def cloud(tmp_path):
    scope = _create(tmp_path)
    app = _app(scope.root, synthetic_cloud=True)
    try:
        project = _call(app, "create_project", name="مصطنع")["id"]
        sessions = {}
        item = _call(app, "memory_remember", project=project, text="synthetic blue triangle secret")["item_id"]
        kept = _call(app, "memory_remember", project=project, text="synthetic green square")["item_id"]
        for mode in ("text", "agent"):
            sid = _call(app, "create_session", project=project, name=mode, mode=mode)["id"]
            sessions[mode] = sid
            _call(app, "ask" if mode == "text" else "agent_ask", project=project, session=sid,
                  turn=uuid.uuid4().hex, message="سؤال مصطنع", files=[])
    finally:
        app.close()
    return {"scope": scope, "root": scope.root, "base": tmp_path.resolve(), "project": project,
            "sessions": sessions, "item": item, "kept": kept, "n": 0}


def export(cloud):
    cloud["n"] += 1
    path = cloud["base"] / f"synthetic-{cloud['n']}.json"
    result = backup.export_workspace(cloud["root"], path)
    return path, result["sha256"], cloud["scope"].raw


def forget(cloud):
    app = _app(cloud["root"], synthetic_cloud=True)
    try:
        _call(app, "memory_forget", project=cloud["project"], item_id=cloud["item"])
    finally:
        app.close()


def test_old_session_checkpoint_restores_with_latest_forget_and_same_scope(cloud, monkeypatch):
    store = BoundStore()
    first = sc.commit_synthetic_checkpoint(*export(cloud), store)
    forget(cloud)
    latest = sc.commit_synthetic_checkpoint(*export(cloud), store)
    assert first["scope"] == ss.checkpoint_scope(cloud["scope"].raw)
    assert first["scope"]["contract_sha256"] == json.loads(cloud["scope"].raw)["sha256"]
    assert latest["previous"] == first["receipt_sha256"] and latest["sequence"] == 2
    assert store.calls.index("bind") < store.calls.index("head") < store.calls.index("put")
    shutil.rmtree(cloud["root"])
    recovered, destination = BoundStore(store.state), cloud["base"] / "restored"
    restore = backup.restore_workspace
    def held(*args, **kwargs):
        assert recovered.locked and recovered.verified
        return restore(*args, **kwargs)
    monkeypatch.setattr(backup, "restore_workspace", held)
    report = sc.restore_synthetic_checkpoint(recovered, destination, cloud["scope"].raw,
                                             receipt_sha256=first["receipt_sha256"])
    assert report["forget_authority_sha256"] == latest["receipt_sha256"]
    assert report["scope"] == first["scope"] and "bind" not in recovered.calls
    assert recovered.calls[0] == "require-bound"
    memory = MemoryStore(destination / "projects" / cloud["project"])
    assert [x["item_id"] for x in memory.items()] == [cloud["kept"]]
    provider = Provider()
    app = _app(destination, provider, synthetic_cloud=True)
    try:
        for mode, sid in cloud["sessions"].items():
            result = _call(app, "ask" if mode == "text" else "agent_ask", project=cloud["project"],
                           session=sid, turn=uuid.uuid4().hex, message="تابع", files=[])
            assert result["status"] == "complete"
    finally:
        app.close()
    assert provider.requests and all(r.data_policy == "internal" for r in provider.requests)
    assert "synthetic blue triangle secret" not in repr(provider.requests)
    assert not recovered.locked


@pytest.mark.parametrize("mode", ["text", "agent"])
def test_legacy_local_sessions_are_refused_before_any_store_call(tmp_path, mode):
    cloud = _create(tmp_path)
    root = tmp_path.resolve() / "local"
    app = _app(root)
    try:
        pid = _call(app, "create_project", name="مصطنع محلي")["id"]
        _call(app, "create_session", project=pid, name=mode, mode=mode)
    finally:
        app.close()
    archive = tmp_path / "local.json"
    sha = backup.export_workspace(root, archive)["sha256"]
    store = BoundStore()
    with pytest.raises(cp.CheckpointError, match="checkpoint_scope_mismatch"):
        sc.commit_synthetic_checkpoint(archive, sha, cloud.raw, store)
    assert store.calls == []


@pytest.mark.parametrize("fault", ["invalid-contract", "different-contract", "corrupt-archive"])
def test_admission_rejects_unapproved_or_changed_bytes_before_store(cloud, fault):
    archive, sha, raw = export(cloud)
    if fault == "invalid-contract":
        raw = b'{}'
    elif fault == "different-contract":
        raw = _create(cloud["base"], "other").raw
    else:
        sha = "0" * 64
    store = BoundStore()
    with pytest.raises((cp.CheckpointError, backup.BackupError)):
        sc.commit_synthetic_checkpoint(archive, sha, raw, store)
    assert store.calls == []


@pytest.mark.parametrize("fault", ["local_only", "regulated", "duplicate", "jsonl", "upload"])
def test_nested_restrictions_and_imports_cannot_hide_under_internal_contract(cloud, fault):
    directory = cloud["root"] / "projects" / cloud["project"] / ("uploads" if fault == "upload" else "outputs")
    directory.mkdir(mode=0o700, exist_ok=True)
    if fault == "duplicate":
        raw = b'{"data_policy":"local_only","data_policy":"internal"}'
    elif fault == "jsonl":
        raw = b'{"text":"synthetic"}\n{"data_policy":"local_only"}\n'
    else:
        raw = json.dumps({"nested": {"data_policy": "internal" if fault == "upload" else fault}}).encode()
    path = directory / "synthetic.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    store = BoundStore()
    with pytest.raises(cp.CheckpointError, match="checkpoint_import_refused|checkpoint_policy_refused"):
        sc.commit_synthetic_checkpoint(*export(cloud), store)
    assert store.calls == []


@pytest.mark.parametrize("phase", ["commit", "restore"])
def test_binding_result_must_match_before_data_access(cloud, phase):
    store = BoundStore()
    archive = export(cloud)
    if phase == "restore":
        sc.commit_synthetic_checkpoint(*archive, store)
    store.calls.clear()
    store.bad_identity = True
    with pytest.raises(cp.CheckpointError, match="checkpoint_scope_mismatch"):
        if phase == "commit":
            sc.commit_synthetic_checkpoint(*archive, store)
        else:
            sc.restore_synthetic_checkpoint(store, cloud["base"] / "no", cloud["scope"].raw)
    assert "head" not in store.calls and "get" not in store.calls and "put" not in store.calls


@pytest.mark.parametrize("where", ["head", "ancestor", "archive"])
def test_foreign_scope_cannot_enter_a_valid_hashed_receipt_chain(cloud, where):
    store = BoundStore()
    first = sc.commit_synthetic_checkpoint(*export(cloud), store)
    latest = sc.commit_synthetic_checkpoint(*export(cloud), store)
    foreign = ss.checkpoint_scope(_create(cloud["base"], "other").raw)
    def receipt(result):
        return {k: v for k, v in result.items() if k not in {"status", "receipt_sha256"}}
    head = receipt(latest)
    if where == "head":
        head["scope"] = foreign
    elif where == "ancestor":
        ancestor = receipt(first)
        ancestor["scope"] = foreign
        raw = canonical_bytes(ancestor)
        head["previous"] = cp._sha(raw)
        store.state["objects"]["receipts/" + cp._sha(raw)] = raw
    else:
        # A valid different contract in an otherwise valid new empty workspace.
        other = _create(cloud["base"], "archive")
        app = _app(other.root, synthetic_cloud=True)
        app.close()
        archive = cloud["base"] / "foreign.json"
        sha = backup.export_workspace(other.root, archive)["sha256"]
        head["archive_sha256"] = sha
        store.state["objects"]["archives/" + sha] = archive.read_bytes()
    store.state["head"] = canonical_bytes(head)
    destination = cloud["base"] / "no"
    with pytest.raises(cp.CheckpointError, match="checkpoint_receipt_invalid|checkpoint_scope_mismatch"):
        sc.restore_synthetic_checkpoint(store, destination, cloud["scope"].raw,
                                       receipt_sha256=head["previous"] if where == "ancestor" else first["receipt_sha256"])
    assert not destination.exists()


def test_forget_regression_and_missing_head_never_reinitialize(cloud):
    store = BoundStore()
    old = export(cloud)
    sc.commit_synthetic_checkpoint(*old, store)
    forget(cloud)
    sc.commit_synthetic_checkpoint(*export(cloud), store)
    with pytest.raises(cp.CheckpointError, match="checkpoint_forget_regression"):
        sc.commit_synthetic_checkpoint(*old, store)
    store.state["head"] = None
    with pytest.raises(cp.CheckpointError, match="checkpoint_head_missing"):
        sc.commit_synthetic_checkpoint(*old, store)


@pytest.mark.parametrize("mode", ["research", "coder", "translate"])
def test_other_agent_modes_are_not_implicitly_admitted(cloud, mode):
    path = cloud["root"] / "projects" / cloud["project"] / "sessions" / cloud["sessions"]["agent"] / "meta.json"
    value = json.loads(path.read_bytes())
    value["mode"] = mode
    path.write_bytes(canonical_bytes(value))
    store = BoundStore()
    with pytest.raises(cp.CheckpointError, match="checkpoint_session_policy_refused"):
        sc.commit_synthetic_checkpoint(*export(cloud), store)
    assert store.calls == []


def test_restore_does_not_claim_unbound_store_or_accept_bad_scope(cloud):
    store = BoundStore()
    destination = cloud["base"] / "no"
    for raw in (b"{}", cloud["scope"].raw):
        with pytest.raises(cp.CheckpointError):
            sc.restore_synthetic_checkpoint(store, destination, raw)
    assert store.calls == [] and not destination.exists()


def test_portable_identity_still_requires_the_real_approval(cloud):
    value = json.loads(cloud["scope"].raw)
    plan = value["record"]["plan"]
    assert ss.checkpoint_scope(cloud["scope"].raw) == {
        "workspace_id": plan["workspace_id"], "contract_sha256": value["sha256"], "target": plan["target"]}
    value["record"]["approval"]["owner_approved"] = False
    value["sha256"] = digest(value["record"])
    with pytest.raises(ss.StorageScopeError, match="cloud_approval_required"):
        ss.checkpoint_scope(canonical_bytes(value))


def test_hub_receipt_size_is_bounded_before_binding_a_branch(tmp_path):
    plan = ss.plan_cloud_workspace({**TARGET, "repo_id": "synthetic/" + "a" * 5000})
    scope = _create(tmp_path, plan=plan)
    app = _app(scope.root, synthetic_cloud=True)
    app.close()
    archive = tmp_path / "large-receipt.json"
    sha = backup.export_workspace(scope.root, archive)["sha256"]
    store = BoundStore()
    with pytest.raises(cp.CheckpointError, match="checkpoint_receipt_limit"):
        sc.commit_synthetic_checkpoint(archive, sha, scope.raw, store)
    assert store.calls == []


@pytest.mark.parametrize("field,value", [("kind", "diwan_public_checkpoint"), ("sequence", True),
    ("previous", "0" * 64), ("tombstones_sha256", "0" * 64)])
def test_receipt_kind_sequence_and_tombstones_are_verified(cloud, field, value):
    store = BoundStore()
    sc.commit_synthetic_checkpoint(*export(cloud), store)
    receipt = json.loads(store.state["head"])
    receipt[field] = value
    store.state["head"] = canonical_bytes(receipt)
    destination = cloud["base"] / "no"
    with pytest.raises(cp.CheckpointError):
        sc.restore_synthetic_checkpoint(store, destination, cloud["scope"].raw)
    assert not destination.exists()


def test_conflicting_tombstones_are_refused_before_store(cloud):
    forget(cloud)
    memory = MemoryStore(cloud["root"] / "projects" / cloud["project"])
    receipt = memory.receipts()[0]
    conflict = {**receipt, "sha256": "0" * 64}
    with memory._receipts.open("a") as stream:
        stream.write(json.dumps(conflict) + "\n")
    store = BoundStore()
    with pytest.raises(cp.CheckpointError, match="checkpoint_conflicting_tombstones"):
        sc.commit_synthetic_checkpoint(*export(cloud), store)
    assert store.calls == []


@pytest.mark.parametrize("fault", ["upload", "corrupt-upload", "conflict", "lost-ack", "lease-exit"])
def test_write_failure_or_lost_ack_never_reports_saved_or_retries(cloud, fault):
    store = BoundStore()
    store.fail = fault
    with pytest.raises(cp.CheckpointError) as caught:
        sc.commit_synthetic_checkpoint(*export(cloud), store)
    uncertain = fault in ("conflict", "lost-ack", "lease-exit")
    assert caught.value.head_may_have_advanced == uncertain
    assert store.calls.count("cas") == int(uncertain)
    assert "private adapter" not in str(caught.value)
    if not uncertain:
        assert store.state["head"] is None


@pytest.mark.parametrize("fault", ["missing-head", "missing-latest", "corrupt-latest", "lease-exit"])
def test_restore_has_no_fallback_and_no_success_before_release(cloud, fault):
    store = BoundStore()
    first = sc.commit_synthetic_checkpoint(*export(cloud), store)
    forget(cloud)
    latest = sc.commit_synthetic_checkpoint(*export(cloud), store)
    key = "archives/" + latest["archive_sha256"]
    if fault == "missing-head":
        store.state["head"] = None
    elif fault == "missing-latest":
        del store.state["objects"][key]
    elif fault == "corrupt-latest":
        store.state["objects"][key] = b"broken"
    else:
        store.fail = fault
    destination = cloud["base"] / "no-success"
    with pytest.raises(cp.CheckpointError):
        sc.restore_synthetic_checkpoint(store, destination, cloud["scope"].raw,
                                       receipt_sha256=first["receipt_sha256"])
    if fault != "lease-exit":
        assert not destination.exists()


def _disk_worker(operation, args, persisted):
    store = BoundStore()
    path = Path(persisted)
    if path.exists():
        value = json.loads(path.read_text())
        store.state.update(objects={k: base64.b64decode(v) for k, v in value["objects"].items()},
                           head=base64.b64decode(value["head"]), scope=base64.b64decode(value["scope"]))
    if operation == "commit":
        for archive, sha, scope in args:
            sc.commit_synthetic_checkpoint(Path(archive), sha, scope, store)
        path.write_bytes(canonical_bytes({"objects": {k: base64.b64encode(v).decode() for k, v in store.state["objects"].items()},
            "head": base64.b64encode(store.state["head"]).decode(),
            "scope": base64.b64encode(store.state["scope"]).decode()}))
    else:
        destination, scope, first = args
        sc.restore_synthetic_checkpoint(store, Path(destination), scope, receipt_sha256=first)


def test_two_fresh_processes_restore_sessions_after_source_loss(cloud):
    first = export(cloud)
    forget(cloud)
    second = export(cloud)
    persisted = cloud["base"] / "synthetic-test-store.json"
    context = multiprocessing.get_context("spawn")
    writer = context.Process(target=_disk_worker, args=("commit", [first, second], str(persisted)))
    writer.start()
    writer.join(30)
    assert writer.exitcode == 0
    value = json.loads(persisted.read_text())
    head = json.loads(base64.b64decode(value["head"]))
    first_sha = head["previous"]
    shutil.rmtree(cloud["root"])
    destination = cloud["base"] / "cold"
    reader = context.Process(target=_disk_worker, args=("restore", (str(destination), cloud["scope"].raw, first_sha), str(persisted)))
    reader.start()
    reader.join(30)
    assert reader.exitcode == 0 and reader.pid != writer.pid
    assert ss.read_storage_scope(destination).raw == cloud["scope"].raw
    assert [x["item_id"] for x in MemoryStore(destination / "projects" / cloud["project"]).items()] == [cloud["kept"]]
    app = _app(destination, synthetic_cloud=True)
    try:
        for sid in cloud["sessions"].values():
            assert _call(app, "history", project=cloud["project"], session=sid, before=None)["turns"]
    finally:
        app.close()
