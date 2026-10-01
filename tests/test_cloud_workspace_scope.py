"""Synthetic-only cloud creation contracts; no provider or storage network calls."""
import copy
import json
import shutil
import uuid

import pytest

from acceptance_m9 import SYNTHETIC_MODEL, SYNTHETIC_VERSION, _answer, _snapshot
from conversation import ChatSession
from conversation.session import ConversationError
from core.canonical import digest
from webui.server import LocalApp, UIError
from workspace_tools import backup, storage_scope as storage


TARGET = {"provider": "huggingface_hub", "repo_type": "dataset",
          "repo_id": "synthetic/private-test", "branch": "synthetic"}


class Provider:
    is_local = True
    model = SYNTHETIC_MODEL
    name = "synthetic-cloud-scope"

    def __init__(self):
        self.requests = []

    def estimate_micros(self, request):
        self.requests.append(request)
        return 0

    def complete(self, request):
        return _answer("جواب مصطنع")


def _create(tmp_path, name="cloud", plan=None):
    authority = tmp_path.resolve()
    plan = plan or storage.plan_cloud_workspace(TARGET)
    scope = storage.create_cloud_workspace(authority / name, plan,
        approval={"owner_approved": True, "plan_sha256": digest(plan)}, claims_root=authority)
    return scope


def _app(root, provider=None, **kwargs):
    provider = provider or Provider()
    return LocalApp(root, model=SYNTHETIC_MODEL, model_version=SYNTHETIC_VERSION,
        provider_factory=lambda: provider, agent_provider_factory=lambda: provider, **kwargs)


def _call(app, action, **kwargs):
    return app.dispatch({"action": action, **kwargs})


def _session(app, mode):
    project = _call(app, "create_project", name="مشروع مصطنع")["id"]
    session = _call(app, "create_session", project=project, name="جلسة مصطنعة", mode=mode)["id"]
    return {"project": project, "session": session}


def _manifest(root, ctx, mode):
    project = root / "projects" / ctx["project"]
    return (project / "agent-control" / ctx["session"] / "manifest.json" if mode == "agent"
            else project / "sessions" / ctx["session"] / "chat" / ctx["session"] / "manifest.json")


def test_approval_and_target_are_exact_before_any_creation(tmp_path):
    plan = storage.plan_cloud_workspace(TARGET)
    good = {"owner_approved": True, "plan_sha256": digest(plan)}
    bad = [None, {}, {**good, "owner_approved": False}, {**good, "owner_approved": 1},
           {**good, "owner_approved": "true"}, {**good, "plan_sha256": "0" * 64},
           {**good, "extra": True}]
    for approval in bad:
        with pytest.raises(storage.StorageScopeError, match="cloud_approval_required"):
            storage.create_cloud_workspace(tmp_path / "cloud", plan, approval=approval, claims_root=tmp_path)
        assert list(tmp_path.iterdir()) == []
    for field, value in (("provider", "unknown"), ("repo_type", "public"),
                         ("repo_id", "../owner"), ("branch", "../main")):
        with pytest.raises(storage.StorageScopeError, match="cloud_scope_invalid"):
            storage.plan_cloud_workspace({**TARGET, field: value})
    changed = copy.deepcopy(plan)
    changed["target"]["branch"] = "different"
    with pytest.raises(storage.StorageScopeError, match="cloud_approval_required"):
        storage.create_cloud_workspace(tmp_path / "cloud", changed, approval=good, claims_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_existing_destinations_and_namespace_reuse_are_refused(tmp_path):
    for name in ("empty", "populated"):
        path = tmp_path / name
        path.mkdir(mode=0o700)
        if name == "populated":
            (path / "local.txt").write_text("local fixture")
        before = _snapshot(path)
        authority_names = sorted(p.name for p in tmp_path.iterdir())
        with pytest.raises(storage.StorageScopeError, match="cloud_destination_exists"):
            _create(tmp_path, name)
        assert _snapshot(path) == before
        assert sorted(p.name for p in tmp_path.iterdir()) == authority_names
    (tmp_path / "link").symlink_to(tmp_path / "missing")
    with pytest.raises(storage.StorageScopeError, match="cloud_destination_exists"):
        _create(tmp_path, "link")
    plan = storage.plan_cloud_workspace(TARGET)
    first = _create(tmp_path, "one", plan)
    shutil.rmtree(first.root)
    with pytest.raises(storage.StorageScopeError, match="cloud_namespace_used"):
        _create(tmp_path, "two", plan)
    assert not (tmp_path / "two").exists()


def test_partial_creation_cannot_reopen_as_local(tmp_path, monkeypatch):
    plan = storage.plan_cloud_workspace(TARGET)
    def fail(*args, **kwargs):
        raise OSError("synthetic write failure")
    monkeypatch.setattr(storage, "_write_json", fail)
    with pytest.raises(OSError):
        _create(tmp_path, plan=plan)
    with pytest.raises(storage.StorageScopeError, match="cloud_creation_incomplete"):
        _app(tmp_path / "cloud")
    with pytest.raises(storage.StorageScopeError, match="cloud_namespace_used"):
        _create(tmp_path, "different", plan)


def test_scope_corruption_and_outside_paths_never_grant_consent(tmp_path):
    scope = _create(tmp_path)
    with pytest.raises(storage.StorageScopeError, match="cloud_scope_path_conflict"):
        ChatSession(tmp_path / "outside", "synthetic", model=SYNTHETIC_MODEL,
                    model_version=SYNTHETIC_VERSION, storage_scope=scope)
    assert not (tmp_path / "outside").exists()
    manifest = scope.root / storage.MANIFEST
    value = json.loads(scope.raw)
    value["sha256"] = "0" * 64
    manifest.write_text(json.dumps(value))
    with pytest.raises(storage.StorageScopeError, match="cloud_scope_invalid"):
        storage.read_storage_scope(scope.root)
    manifest.write_bytes(scope.raw)
    with pytest.raises(storage.StorageScopeError, match="cloud_creation_authority_required"):
        storage.create_cloud_workspace(tmp_path.parent / "outside", value["record"]["plan"],
            approval=value["record"]["approval"], claims_root=tmp_path)


@pytest.mark.parametrize("mode", ["text", "agent"])
def test_new_sessions_bind_consent_and_internal_request_and_receipt(tmp_path, mode):
    scope = _create(tmp_path)
    provider = Provider()
    app = _app(scope.root, provider, synthetic_cloud=True)
    try:
        ctx = _session(app, mode)
        result = _call(app, "agent_ask" if mode == "agent" else "ask", **ctx,
                       turn=uuid.uuid4().hex, message="سؤال مصطنع", files=[])
        assert result["status"] == "complete"
    finally:
        app.close()
    assert provider.requests and all(r.data_policy == "internal" for r in provider.requests)
    manifest = _manifest(scope.root, ctx, mode)
    config = json.loads(manifest.read_text())
    assert config["data_policy"] == "internal"
    assert config["storage"] == scope.binding(manifest)
    rows = [json.loads(line)["record"] for line in (manifest.parent / "calls.jsonl").read_text().splitlines()]
    assert rows and all(row["data_policy"] == "internal" for row in rows)
    before = _snapshot(scope.root)
    reopened = _app(scope.root, synthetic_cloud=True)
    try:
        history = _call(reopened, "history", **ctx, before=None)
        assert history["turns"]
    finally:
        reopened.close()
    assert _snapshot(scope.root) == before


@pytest.mark.parametrize("mode", ["text", "agent"])
def test_legacy_sessions_do_not_gain_a_cloud_classification(tmp_path, mode):
    root = tmp_path.resolve() / "local"
    provider = Provider()
    app = _app(root, provider)
    try:
        ctx = _session(app, mode)
        _call(app, "agent_ask" if mode == "agent" else "ask", **ctx,
              turn=uuid.uuid4().hex, message="مصطنع محلي", files=[])
    finally:
        app.close()
    assert all(r.data_policy == "local_only" for r in provider.requests)
    before = _snapshot(root)
    with pytest.raises(storage.StorageScopeError, match="cloud_destination_exists"):
        _create(tmp_path, "local")
    assert _snapshot(root) == before
    config = json.loads(_manifest(root, ctx, mode).read_text())
    assert "storage" not in config
    cloud = _create(tmp_path)
    shutil.copytree(root / "projects", cloud.root / "projects")
    app = _app(cloud.root, synthetic_cloud=True)
    try:
        with pytest.raises(ConversationError):
            _call(app, "history", **ctx, before=None)
    finally:
        app.close()
    with pytest.raises(backup.BackupError, match="backup_cloud_session_invalid"):
        backup.export_workspace(cloud.root, tmp_path / "mixed.json")


@pytest.mark.parametrize("mode", ["text", "agent"])
def test_cloud_sessions_cannot_move_to_another_scope_or_lose_their_approval(tmp_path, mode):
    one, two = _create(tmp_path, "one"), _create(tmp_path, "two")
    app = _app(one.root, synthetic_cloud=True)
    try:
        ctx = _session(app, mode)
    finally:
        app.close()
    shutil.copytree(one.root / "projects", two.root / "projects")
    app = _app(two.root, synthetic_cloud=True)
    try:
        with pytest.raises(ConversationError):
            _call(app, "history", **ctx, before=None)
    finally:
        app.close()
    app = _app(one.root, synthetic_cloud=True)
    try:
        (one.root / storage.MANIFEST).unlink()
        with pytest.raises(UIError, match="cloud_scope_changed"):
            _call(app, "history", **ctx, before=None)
    finally:
        app.close()
    app = _app(one.root)
    try:
        with pytest.raises(ConversationError):
            _call(app, "history", **ctx, before=None)
    finally:
        app.close()


@pytest.mark.parametrize("mode", ["text", "agent"])
def test_internal_sessions_still_require_the_real_local_provider_contract(tmp_path, mode):
    scope = _create(tmp_path)
    provider = Provider()
    app = _app(scope.root, provider, synthetic_cloud=True)
    try:
        ctx = _session(app, mode)
        project = app.project(ctx["project"])
        session = (app.agent_session(project, ctx["session"]) if mode == "agent"
                   else app.session(project, ctx["session"]))
        for field, value in (("is_local", False), ("model", "synthetic:cloud"),
                             ("base_url", "https://remote.invalid")):
            setattr(provider, field, value)
            with pytest.raises(ConversationError, match="policy_requires_local"):
                (session.start_turn if mode == "agent" else session.turn)(uuid.uuid4().hex, "مصطنع", provider)
            delattr(provider, field)
        assert provider.requests == []
    finally:
        app.close()


@pytest.mark.parametrize("mode", ["text", "agent"])
def test_internal_request_cannot_hide_a_cloud_model_behind_a_provider_without_model(tmp_path, mode):
    scope = _create(tmp_path)
    provider = Provider()
    provider.model = None
    app = _app(scope.root, provider, synthetic_cloud=True)
    app.model = "synthetic:cloud"
    try:
        ctx = _session(app, mode)
        project = app.project(ctx["project"])
        session = (app.agent_session(project, ctx["session"]) if mode == "agent"
                   else app.session(project, ctx["session"]))
        with pytest.raises(ConversationError, match="policy_requires_local"):
            (session.start_turn if mode == "agent" else session.turn)(uuid.uuid4().hex, "مصطنع", provider)
        assert provider.requests == []
    finally:
        app.close()


def test_cloud_scope_requires_explicit_test_bootstrap_and_disallows_file_ingress(tmp_path):
    scope = _create(tmp_path)
    with pytest.raises(UIError, match="cloud_scope_requires_test_bootstrap"):
        _app(scope.root)
    app = _app(scope.root, synthetic_cloud=True)
    try:
        for action in ("upload", "agent_import", "ask_media"):
            with pytest.raises(UIError, match="cloud_file_ingress_disabled"):
                _call(app, action)
        project = _call(app, "create_project", name="مصطنع")["id"]
        with pytest.raises(UIError, match="cloud_media_not_supported"):
            _call(app, "create_session", project=project, name="مصطنع", mode="media")
        for policy in ("local_only", "regulated", "public"):
            with pytest.raises(UIError, match="cloud_input_policy_refused"):
                _call(app, "ask", data_policy=policy)
    finally:
        app.close()


@pytest.mark.parametrize("mode", ["text", "agent"])
def test_offline_backup_restore_preserves_cloud_consent_and_session_binding(tmp_path, mode):
    scope = _create(tmp_path)
    app = _app(scope.root, synthetic_cloud=True)
    try:
        ctx = _session(app, mode)
        _call(app, "agent_ask" if mode == "agent" else "ask", **ctx,
              turn=uuid.uuid4().hex, message="مصطنع", files=[])
    finally:
        app.close()
    archive = tmp_path / "backup.json"
    result = backup.export_workspace(scope.root, archive)
    restored = tmp_path / "restored"
    backup.restore_workspace(archive, restored, result["sha256"], tombstones_from=None)
    assert storage.read_storage_scope(restored).raw == scope.raw
    assert _manifest(restored, ctx, mode).read_bytes() == _manifest(scope.root, ctx, mode).read_bytes()
    app = _app(restored, synthetic_cloud=True)
    try:
        assert _call(app, "history", **ctx, before=None)["turns"]
    finally:
        app.close()
