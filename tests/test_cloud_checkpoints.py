"""Synthetic-only protocol adapter: no credentials, network, bucket, or inference."""
from contextlib import contextmanager
import hashlib
import json
import shutil
import threading

import pytest

from evaluation.memory_runner import _Wired
from core.canonical import canonical_bytes
from memory.store import MemoryStore
from workspace_tools import backup, checkpoints as cp


class TestStore:
    __test__ = False

    def __init__(self, state=None):
        self.state = state if state is not None else {"objects": {}, "head": None, "lock": threading.RLock()}
        self.calls, self.locked, self.fail = [], False, None

    @contextmanager
    def exclusive(self):
        self.calls.append("lease")
        with self.state["lock"]:
            assert not self.locked
            self.locked = True
            try:
                yield
            finally:
                self.locked = False
        if self.fail == "lease-exit":
            raise OSError("private adapter diagnostics must not escape")

    def read_head(self):
        assert self.locked
        self.calls.append("head")
        return self.state["head"]

    def is_pristine(self):
        assert self.locked
        return not self.state["objects"] and self.state["head"] is None

    def get(self, key):
        assert self.locked
        self.calls.append("get")
        return self.state["objects"][key]

    def put_immutable(self, key, value):
        assert self.locked
        self.calls.append("put")
        if self.fail == "upload":
            raise OSError("private adapter diagnostics must not escape")
        if self.fail == "corrupt-upload":
            value = b"damaged"
        assert self.state["objects"].get(key, value) == value
        self.state["objects"][key] = value

    def compare_and_swap_head(self, expected, value):
        assert self.locked
        self.calls.append("cas")
        if self.fail == "conflict":
            return False
        assert self.state["head"] == expected
        self.state["head"] = value
        if self.fail == "lost-ack":
            raise OSError("private adapter diagnostics must not escape")
        return True


@pytest.fixture
def sample(tmp_path):
    base = tmp_path.resolve()
    base.chmod(0o700)
    wired = _Wired(base / "synthetic-public")
    project = wired.project("checkpoint")['id']
    item = wired.api("memory_remember", project=project, text="synthetic public blue triangle")['item_id']
    kept = wired.api("memory_remember", project=project, text="synthetic public green square")['item_id']
    wired.close()
    return {"base": base, "root": wired.root, "project": project, "item": item, "kept": kept, "n": 0}


def exported(sample):
    sample["n"] += 1
    path = sample["base"] / f"archive-{sample['n']}.json"
    report = backup.export_workspace(sample["root"], path)
    # Test-owned synthetic bytes only. Production deliberately has no function
    # that manufactures public grants from an arbitrary owner workspace.
    approval = {"archive_sha256": report["sha256"], "files": {
        f["path"]: {"sha256": f["sha256"], "data_policy": "public"}
        for f in json.loads(path.read_bytes())["files"]}}
    return path, report["sha256"], approval


def memory(sample, root=None):
    return MemoryStore((root or sample["root"]) / "projects" / sample["project"])


def test_cold_restore_of_old_checkpoint_uses_latest_forget_authority(sample, monkeypatch):
    store = TestStore()
    first = cp.commit_checkpoint(*exported(sample), store)
    memory(sample).forget(sample["item"])
    latest = cp.commit_checkpoint(*exported(sample), store)
    assert latest["sequence"] == 2 and latest["previous"] == first["receipt_sha256"]
    assert first["receipt_sha256"] == hashlib.sha256(store.state["objects"]["receipts/" + first["receipt_sha256"]]).hexdigest()
    assert store.calls.index("cas") > store.calls.index("get")
    # Simulate losing the entire ephemeral root and process, with only the
    # remote adapter state remaining. This is not a cloud durability claim.
    shutil.rmtree(sample["root"])
    destination = sample["base"] / "cold-restored"
    recovered = TestStore(store.state)
    original = backup.restore_workspace

    def restore_under_lease(*args, **kwargs):
        assert recovered.locked  # Includes materialization, not just reading the head.
        return original(*args, **kwargs)

    monkeypatch.setattr(backup, "restore_workspace", restore_under_lease)
    result = cp.restore_checkpoint(recovered, destination, receipt_sha256=first["receipt_sha256"])
    assert result["forget_authority_sha256"] == latest["receipt_sha256"]
    assert result["backup"]["memory"]["items_in_archive"] == 2
    assert [item["item_id"] for item in memory(sample, destination).items()] == [sample["kept"]]
    assert memory(sample, destination).receipts()[0]["item_id"] == sample["item"]


def test_an_old_checkpoint_cannot_erase_a_committed_forget_receipt(sample):
    store = TestStore()
    old = exported(sample)
    cp.commit_checkpoint(*old, store)
    memory(sample).forget(sample["item"])
    cp.commit_checkpoint(*exported(sample), store)
    head = store.state["head"]
    with pytest.raises(cp.CheckpointError, match="checkpoint_forget_regression"):
        cp.commit_checkpoint(*old, store)
    assert store.state["head"] == head


def test_losing_the_head_cannot_silently_reinitialize_old_memory(sample):
    store = TestStore()
    old = exported(sample)
    cp.commit_checkpoint(*old, store)
    memory(sample).forget(sample["item"])
    cp.commit_checkpoint(*exported(sample), store)
    store.state["head"] = None
    with pytest.raises(cp.CheckpointError, match="checkpoint_head_missing"):
        cp.commit_checkpoint(*old, store)
    assert store.state["head"] is None


@pytest.mark.parametrize("fault", ["missing-file", "missing-metadata"])
def test_invalid_archive_fails_by_code_without_leaking_source_paths(sample, fault):
    archive, sha, approval = exported(sample)
    if fault == "missing-file":
        archive.unlink()
    else:
        bundle = json.loads(archive.read_bytes())
        bundle["files"] = [f for f in bundle["files"] if not f["path"].endswith("/meta.json")]
        raw = canonical_bytes(bundle)
        archive.write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
    store = TestStore()
    with pytest.raises(backup.BackupError) as error:
        cp.commit_checkpoint(archive, sha, approval, store)
    assert error.value.code == "backup_invalid"
    assert str(sample["base"]) not in str(error.value)
    assert store.calls == []


@pytest.mark.parametrize("mode", ["text", "agent"])
def test_a_public_grant_cannot_reclassify_existing_sessions(sample, mode):
    wired = _Wired(sample["root"])
    try:
        wired.api("create_session", project=sample["project"], name="synthetic", mode=mode)
    finally:
        wired.close()
    store = TestStore()
    with pytest.raises(cp.CheckpointError, match="checkpoint_session_policy_refused"):
        cp.commit_checkpoint(*exported(sample), store)
    assert store.calls == []


@pytest.mark.parametrize("policy", ["local_only", "regulated", "duplicate-key", "jsonl"])
def test_embedded_private_policy_overrides_public_grants_before_any_adapter_call(sample, policy):
    # Ordinary public-labelled attachment with a nested explicit restriction.
    uploads = sample["root"] / "projects" / sample["project"] / "uploads"
    uploads.mkdir(mode=0o700, exist_ok=True)
    path = uploads / "synthetic.json"
    if policy == "duplicate-key":
        payload = '{"data_policy":"local_only","data_policy":"public"}'
    elif policy == "jsonl":
        payload = '{"text":"fixture"}\n{"data_policy":"local_only"}\n'
    else:
        payload = json.dumps({"nested": [{"data_policy": policy, "text": "fixture"}]})
    path.write_text(payload)
    path.chmod(0o600)
    store = TestStore()
    with pytest.raises(cp.CheckpointError, match="checkpoint_policy_refused"):
        cp.commit_checkpoint(*exported(sample), store)
    assert store.calls == []


@pytest.mark.parametrize("change", ["missing-file", "stale-file", "stale-archive", "local-only"])
def test_unapproved_or_reclassified_bytes_never_reach_adapter(sample, change):
    archive, sha, approval = exported(sample)
    key = next(k for k in approval["files"] if "/items/" in k)
    if change == "missing-file":
        del approval["files"][key]
    elif change == "stale-file":
        approval["files"][key]["sha256"] = "0" * 64
    elif change == "stale-archive":
        approval["archive_sha256"] = "0" * 64
    else:
        approval["files"][key]["data_policy"] = "local_only"
    store = TestStore()
    with pytest.raises(cp.CheckpointError):
        cp.commit_checkpoint(archive, sha, approval, store)
    assert store.calls == []


@pytest.mark.parametrize("fault,code", [("upload", "checkpoint_store_failure"),
                                        ("corrupt-upload", "checkpoint_object_corrupt")])
def test_failed_upload_does_not_publish_a_receipt_or_claim_success(sample, fault, code):
    store = TestStore()
    store.fail = fault
    with pytest.raises(cp.CheckpointError) as error:
        cp.commit_checkpoint(*exported(sample), store)
    assert error.value.code == code
    assert not error.value.head_may_have_advanced
    assert "private adapter" not in str(error.value)
    assert store.state["head"] is None and "cas" not in store.calls


@pytest.mark.parametrize("fault", ["conflict", "lost-ack", "lease-exit"])
def test_publication_uncertainty_is_named_and_not_retried(sample, fault):
    store = TestStore()
    store.fail = fault
    with pytest.raises(cp.CheckpointError) as error:
        cp.commit_checkpoint(*exported(sample), store)
    assert error.value.code == "checkpoint_commit_uncertain"
    assert error.value.head_may_have_advanced
    assert store.calls.count("cas") == 1


@pytest.mark.parametrize("fault", ["missing-head", "corrupt-head", "missing-latest", "corrupt-latest"])
def test_missing_or_corrupt_latest_never_falls_back_to_old_forget_state(sample, fault):
    store = TestStore()
    first = cp.commit_checkpoint(*exported(sample), store)
    memory(sample).forget(sample["item"])
    latest = cp.commit_checkpoint(*exported(sample), store)
    key = "archives/" + latest["archive_sha256"]
    if fault == "missing-head":
        store.state["head"] = None
    elif fault == "corrupt-head":
        store.state["head"] = b"{}"
    elif fault == "missing-latest":
        del store.state["objects"][key]
    else:
        store.state["objects"][key] = b"broken"
    destination = sample["base"] / "must-not-exist"
    with pytest.raises(cp.CheckpointError):
        cp.restore_checkpoint(store, destination, receipt_sha256=first["receipt_sha256"])
    assert not destination.exists()


def test_an_unrelated_valid_receipt_cannot_choose_a_different_workspace(sample):
    store = TestStore()
    first = cp.commit_checkpoint(*exported(sample), store)
    separate = TestStore()
    memory(sample).remember("synthetic independent data", consent="owner")
    foreign = cp.commit_checkpoint(*exported(sample), separate)
    store.state["objects"].update(separate.state["objects"])
    destination = sample["base"] / "foreign-restored"
    with pytest.raises(cp.CheckpointError, match="checkpoint_not_ancestor"):
        cp.restore_checkpoint(store, destination, receipt_sha256=foreign["receipt_sha256"])
    assert first["receipt_sha256"] != foreign["receipt_sha256"]
    assert not destination.exists()
