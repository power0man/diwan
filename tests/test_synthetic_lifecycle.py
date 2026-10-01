"""Closed-app durability and concurrency proof; all content and IO are synthetic."""
from contextlib import contextmanager
import json
import shutil
import threading
import uuid

import pytest

from acceptance_m9 import SYNTHETIC_MODEL, SYNTHETIC_VERSION
from core.canonical import canonical_bytes, digest
from tests.test_cloud_workspace_scope import Provider, TARGET
from tests.test_synthetic_checkpoints import BoundStore
from webui import synthetic_lifecycle as life
from workspace_tools import backup, checkpoints as cp, storage_scope as ss


def options(provider=None):
    provider = provider or Provider()
    return {"model": SYNTHETIC_MODEL, "model_version": SYNTHETIC_VERSION,
            "provider_factory": lambda: provider, "agent_provider_factory": lambda: provider}


def create(tmp_path, *, store=None, app_options=None):
    plan = ss.plan_cloud_workspace(TARGET)
    return life.SyntheticLifecycle.create(tmp_path.resolve() / "cloud", plan,
        approval={"owner_approved": True, "plan_sha256": digest(plan)},
        claims_root=tmp_path.resolve(), store=store or BoundStore(), app_options=app_options or options())


def call(owner, action, **kwargs):
    return owner.dispatch({"action": action, **kwargs})


@pytest.fixture
def cloud(tmp_path):
    store, provider = BoundStore(), Provider()
    owner = create(tmp_path, store=store, app_options=options(provider))
    pid = call(owner, "create_project", name="مصطنع")["id"]
    item = call(owner, "memory_remember", project=pid, text="synthetic blue triangle secret")["item_id"]
    kept = call(owner, "memory_remember", project=pid, text="synthetic green square")["item_id"]
    sessions = {}
    for mode in ("text", "agent"):
        sid = call(owner, "create_session", project=pid, name=mode, mode=mode)["id"]
        sessions[mode] = sid
        call(owner, "ask" if mode == "text" else "agent_ask", project=pid, session=sid,
             turn=uuid.uuid4().hex, message="سؤال مصطنع", files=[])
    yield {"owner": owner, "store": store, "provider": provider, "project": pid,
           "item": item, "kept": kept, "sessions": sessions, "root": tmp_path.resolve() / "cloud"}
    owner.close()


def forget(cloud):
    return call(cloud["owner"], "memory_forget", project=cloud["project"], item_id=cloud["item"])


def test_confirmed_forget_survives_source_loss_and_ancestor_restore(cloud, tmp_path, monkeypatch):
    owner, store = cloud["owner"], cloud["store"]
    scope = ss.read_storage_scope(cloud["root"]).raw
    export = backup.export_workspace
    closed_exports = []
    def closed(root, archive):
        assert owner._app is None and owner.state == "saving"
        result = export(root, archive)  # Real app.lock and archive validation.
        closed_exports.append(result["sha256"])
        return result
    monkeypatch.setattr(backup, "export_workspace", closed)
    first = owner.save()
    reply = forget(cloud)
    assert reply["status"] == "forgotten" and reply["durability"]["status"] == "saved"
    assert reply["durability"]["receipt_sha256"] == cp._sha(store.state["head"])
    assert len(closed_exports) == 2 and not store.locked
    owner.close()
    shutil.rmtree(cloud["root"])
    provider = Provider()
    restored = life.SyntheticLifecycle.restore(tmp_path.resolve() / "restored", scope,
        store=BoundStore(store.state), app_options=options(provider), receipt_sha256=first["receipt_sha256"])
    try:
        assert [x["item_id"] for x in call(restored, "memory", project=cloud["project"])["items"]] == [cloud["kept"]]
        for mode, sid in cloud["sessions"].items():
            result = call(restored, "ask" if mode == "text" else "agent_ask", project=cloud["project"],
                          session=sid, turn=uuid.uuid4().hex, message="تابع", files=[])
            assert result["status"] == "complete"
        assert provider.requests and all(r.data_policy == "internal" for r in provider.requests)
        assert "synthetic blue triangle secret" not in repr(provider.requests)
    finally:
        restored.close()


def test_forget_ack_waits_for_release_and_rejects_concurrent_requests(cloud):
    owner, original = cloud["owner"], cloud["store"]
    owner.save()
    reached, release = threading.Event(), threading.Event()
    class PausedRelease(BoundStore):
        @contextmanager
        def exclusive(self, **kwargs):
            with super().exclusive(**kwargs):
                yield
                reached.set()
                assert release.wait(10)
    owner._store = paused = PausedRelease(original.state)
    replies = []
    thread = threading.Thread(target=lambda: replies.append(forget(cloud)))
    thread.start()
    try:
        assert reached.wait(10) and paused.locked
        assert replies == [] and owner.state == "saving"
        for operation in (owner.save, lambda: call(owner, "memory", project=cloud["project"])):
            with pytest.raises(life.LifecycleError, match="cloud_lifecycle_busy"):
                operation()
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and len(replies) == 1
    assert replies[0]["durability"]["receipt_sha256"] == cp._sha(paused.state["head"])
    assert owner.state == "ready" and not paused.locked


@pytest.mark.parametrize("fault", ["upload", "corrupt-upload", "conflict", "lost-ack", "lease-exit"])
def test_forget_failure_never_acknowledges_reopens_or_retries(cloud, fault):
    owner, store = cloud["owner"], cloud["store"]
    owner.save()
    store.fail = fault
    with pytest.raises(life.LifecycleError, match="cloud_forget_unconfirmed"):
        forget(cloud)
    assert owner.state == "blocked" and owner._app is None
    calls = list(store.calls)
    for operation in (owner.save, lambda: forget(cloud), lambda: call(owner, "projects")):
        with pytest.raises(life.LifecycleError, match="cloud_lifecycle_closed"):
            operation()
    assert store.calls == calls


@pytest.mark.parametrize("kind", ["active", "active-agent", "generation"])
def test_generation_busy_refuses_save_and_forget_before_mutation(cloud, kind):
    owner, app = cloud["owner"], cloud["owner"]._app
    before = list(cloud["store"].calls)
    if kind == "active":
        app.active = (cloud["project"], "synthetic", "turn")
    elif kind == "active-agent":
        app.active_agent_session = object()
    else:
        assert app.generation.acquire(blocking=False)
    try:
        for operation in (owner.save, lambda: forget(cloud),
                          lambda: call(owner, "memory_remember", project=cloud["project"], text="لا يضاف")):
            with pytest.raises(life.LifecycleError, match="cloud_generation_busy"):
                operation()
        assert owner.state == "ready" and owner._app is app
        assert cloud["store"].calls == before
    finally:
        app.active = app.active_agent_session = None
        if kind == "generation":
            app.generation.release()
    assert cloud["item"] in {x["item_id"] for x in call(owner, "memory", project=cloud["project"])["items"]}


def test_real_generation_holds_gate_until_provider_finishes(cloud):
    entered, release = threading.Event(), threading.Event()
    provider = cloud["provider"]
    complete = provider.complete
    def held(request):
        entered.set()
        assert release.wait(10)
        return complete(request)
    provider.complete = held
    replies = []
    thread = threading.Thread(target=lambda: replies.append(call(cloud["owner"], "ask",
        project=cloud["project"], session=cloud["sessions"]["text"], turn=uuid.uuid4().hex,
        message="طلب مصطنع ينتظر", files=[])))
    thread.start()
    try:
        assert entered.wait(10)
        before = list(cloud["store"].calls)
        for operation in (cloud["owner"].save, lambda: forget(cloud)):
            with pytest.raises(life.LifecycleError, match="cloud_lifecycle_busy"):
                operation()
        assert cloud["store"].calls == before
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and len(replies) == 1 and replies[0]["status"] == "complete"


def test_mutated_contract_blocks_before_any_dispatch_or_remote_call(cloud):
    before = list(cloud["store"].calls)
    raw = json.loads(ss.read_storage_scope(cloud["root"]).raw)
    raw["record"]["plan"]["workspace_id"] = "f" * 32
    raw["record"]["approval"]["plan_sha256"] = digest(raw["record"]["plan"])
    raw["sha256"] = digest(raw["record"])
    (cloud["root"] / ss.MANIFEST).write_bytes(canonical_bytes(raw))
    with pytest.raises(life.LifecycleError, match="cloud_scope_changed"):
        call(cloud["owner"], "create_project", name="لا ينشأ")
    assert cloud["owner"].state == "blocked" and cloud["owner"]._app is None
    assert cloud["store"].calls == before


@pytest.mark.parametrize("extra", ["root", "synthetic_cloud", "store", "committer", "app"])
def test_bootstrap_cannot_accept_borrowed_app_or_replace_trusted_parameters(tmp_path, extra):
    store = BoundStore()
    with pytest.raises(life.LifecycleError, match="cloud_bootstrap_invalid"):
        create(tmp_path, store=store, app_options={**options(), extra: object()})
    assert list(tmp_path.iterdir()) == [] and store.calls == []


def test_existing_root_is_never_adopted_and_closed_coordinator_never_dispatches(cloud, tmp_path):
    owner = cloud["owner"]
    owner.close()
    with pytest.raises(ss.StorageScopeError, match="cloud_destination_exists"):
        create(tmp_path)
    for operation in (owner.save, lambda: call(owner, "projects")):
        with pytest.raises(life.LifecycleError, match="cloud_lifecycle_closed"):
            operation()
    assert owner.state == "closed"


@pytest.mark.parametrize("phase", ["export", "reopen"])
def test_post_forget_local_failure_is_also_fail_closed(cloud, monkeypatch, phase):
    def fail(*args, **kwargs):
        raise OSError("private test-only diagnostic")
    monkeypatch.setattr(backup if phase == "export" else life,
                        "export_workspace" if phase == "export" else "LocalApp", fail)
    with pytest.raises(life.LifecycleError, match="cloud_forget_unconfirmed") as caught:
        forget(cloud)
    assert "private" not in str(caught.value)
    assert cloud["owner"].state == "blocked" and cloud["owner"]._app is None
    assert (cloud["store"].state["head"] is not None) == (phase == "reopen")


def test_failed_restore_release_never_opens_the_restored_app(cloud, tmp_path, monkeypatch):
    cloud["owner"].save()
    scope = ss.read_storage_scope(cloud["root"]).raw
    calls = []
    monkeypatch.setattr(life, "LocalApp", lambda *args, **kwargs: calls.append(True))
    cloud["store"].fail = "lease-exit"
    with pytest.raises(cp.CheckpointError):
        life.SyntheticLifecycle.restore(tmp_path.resolve() / "unconfirmed", scope,
            store=cloud["store"], app_options=options())
    assert calls == []


@pytest.mark.parametrize("field,value", [("model", ""), ("model_version", None),
    ("provider_factory", None), ("agent_provider_factory", None)])
def test_invalid_provider_configuration_fails_before_creation(tmp_path, field, value):
    with pytest.raises(life.LifecycleError, match="cloud_bootstrap_invalid"):
        create(tmp_path, app_options={**options(), field: value})
    assert list(tmp_path.iterdir()) == []


def test_wrong_app_root_is_closed_and_never_adopted(tmp_path, monkeypatch):
    original, opened = life.LocalApp, []
    def wrong(root, **kwargs):
        app = original(tmp_path.resolve() / "wrong-root", **kwargs)
        opened.append(app)
        return app
    monkeypatch.setattr(life, "LocalApp", wrong)
    with pytest.raises(life.LifecycleError, match="cloud_bootstrap_invalid"):
        create(tmp_path)
    assert len(opened) == 1
    import os
    with pytest.raises(OSError):
        os.fstat(opened[0].lease)


@pytest.mark.parametrize("phase", ["forget", "commit", "restore"])
def test_unconfirmed_result_never_becomes_success(cloud, tmp_path, monkeypatch, phase):
    owner = cloud["owner"]
    if phase == "forget":
        dispatch = owner._app.dispatch
        monkeypatch.setattr(owner._app, "dispatch", lambda request: {**dispatch(request), "status": "uncertain"})
        operation = lambda: forget(cloud)
        code = "cloud_forget_unconfirmed"
    elif phase == "commit":
        commit = life.sc._commit_prepared
        monkeypatch.setattr(life.sc, "_commit_prepared",
                            lambda *args: {**commit(*args), "status": "uncertain"})
        operation, code = owner.save, "cloud_checkpoint_unconfirmed"
    else:
        owner.save()
        scope = ss.read_storage_scope(cloud["root"]).raw
        restore = life.sc.restore_synthetic_checkpoint
        monkeypatch.setattr(life.sc, "restore_synthetic_checkpoint",
                            lambda *args, **kwargs: {**restore(*args, **kwargs), "status": "uncertain"})
        operation = lambda: life.SyntheticLifecycle.restore(tmp_path.resolve() / "unconfirmed", scope,
            store=cloud["store"], app_options=options())
        code = "cloud_restore_unconfirmed"
    with pytest.raises(life.LifecycleError, match=code):
        operation()
    if phase != "restore":
        assert owner.state == "blocked" and owner._app is None


@pytest.mark.parametrize("payload", [None, {}, {"action": None}])
def test_invalid_request_cannot_enter_app_or_checkpoint(cloud, payload):
    before = list(cloud["store"].calls)
    with pytest.raises(life.LifecycleError, match="cloud_request_invalid"):
        cloud["owner"].dispatch(payload)
    assert cloud["owner"].state == "ready" and cloud["store"].calls == before


def test_restored_peer_refuses_reuse_after_another_coordinator_confirms_forget(cloud, tmp_path):
    first = cloud["owner"].save()
    scope = ss.read_storage_scope(cloud["root"]).raw
    peer = life.SyntheticLifecycle.restore(tmp_path.resolve() / "peer", scope,
        store=BoundStore(cloud["store"].state), app_options=options(), receipt_sha256=first["receipt_sha256"])
    try:
        assert forget(cloud)["durability"]["status"] == "saved"
        with pytest.raises(life.LifecycleError, match="cloud_checkpoint_stale"):
            call(peer, "memory", project=cloud["project"])
        assert peer.state == "blocked"
    finally:
        peer.close()


def test_remote_lease_covers_actual_model_reuse(cloud):
    provider, store = cloud["provider"], cloud["store"]
    complete, observed = provider.complete, []
    def held(request):
        observed.append(store.locked and store.state["lock"]._is_owned())
        return complete(request)
    provider.complete = held
    result = call(cloud["owner"], "ask", project=cloud["project"], session=cloud["sessions"]["text"],
                  turn=uuid.uuid4().hex, message="إعادة استخدام مصطنعة", files=[])
    assert result["status"] == "complete" and observed == [True]
    assert not store.locked


@pytest.mark.parametrize("action", ["memory", "ask", "agent_ask"])
@pytest.mark.parametrize("fault", ["lease-exit", "scope", "head-missing"])
def test_remote_authority_failure_blocks_before_reuse_or_ack(cloud, fault, action):
    cloud["owner"].save()
    if fault == "lease-exit":
        cloud["store"].fail = fault
        code = "cloud_storage_unconfirmed"
    elif fault == "scope":
        cloud["store"].bad_identity = True
        code = "cloud_scope_changed"
    else:
        cloud["store"].state["head"] = None
        code = "cloud_checkpoint_stale"
    args = {"project": cloud["project"]}
    if action != "memory":
        args.update(session=cloud["sessions"]["text" if action == "ask" else "agent"],
                    turn=uuid.uuid4().hex, message="لا إقرار بلا تحرير", files=[])
    with pytest.raises(life.LifecycleError, match=code):
        call(cloud["owner"], action, **args)
    assert cloud["owner"].state == "blocked" and cloud["owner"]._app is None


def test_headless_objects_cannot_be_adopted_as_a_new_workspace(cloud):
    cloud["store"].state["objects"]["archives/orphan"] = b"synthetic orphan"
    with pytest.raises(life.LifecycleError, match="cloud_checkpoint_stale"):
        call(cloud["owner"], "memory", project=cloud["project"])
    assert cloud["owner"].state == "blocked" and cloud["owner"]._app is None
