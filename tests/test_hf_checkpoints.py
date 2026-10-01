"""Synthetic protocol regressions; live Hub evidence is separate from these tests."""
from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import json
import threading
import urllib.error

import pytest

from core.canonical import canonical_bytes
from workspace_tools import checkpoints as cp, hf_checkpoints as hf
from tests.test_cloud_checkpoints import sample, exported, memory  # synthetic fixture only

NS = "ab" * 16


class Remote:
    def __init__(self):
        self.lock = threading.Lock()
        self.revision = "0" * 40
        self.versions = {self.revision: {}}
        self.commits = 0
        self.lose_ack = False

    def snapshot(self):
        with self.lock:
            return self.revision, set(self.versions[self.revision])

    def read(self, revision, path):
        return self.versions[revision][path]

    def commit(self, revision, files):
        with self.lock:
            if revision != self.revision:
                raise hf.StoreError("hf_head_conflict")
            self.commits += 1
            self.revision = f"{self.commits:040x}"
            self.versions[self.revision] = {**self.versions[revision], **files}
            if self.lose_ack:
                self.lose_ack = False
                raise OSError("synthetic lost response, never an owner payload")
            return self.revision


def store(remote=None):
    return hf.HubCheckpointStore(remote or Remote(), NS)


def test_cold_adapter_restore_keeps_latest_forget_authority(sample):
    remote = Remote()
    first = cp.commit_checkpoint(*exported(sample), store(remote))
    memory(sample).forget(sample["item"])
    cp.commit_checkpoint(*exported(sample), store(remote))
    restored = sample["base"] / "cold"
    report = cp.restore_checkpoint(store(remote), restored, receipt_sha256=first["receipt_sha256"])
    assert report["status"] == "restored"
    snapshot = memory(sample, restored).items()
    assert "synthetic public blue triangle" not in json.dumps(snapshot)
    assert "synthetic public green square" in json.dumps(snapshot)
    assert store(remote)._decode(remote.read(*remote.snapshot()[:1], store(remote).control))["owner"] is None


def test_holder_does_not_expire_and_second_client_is_busy():
    remote = Remote()
    one, two = store(remote), store(remote)
    with one.exclusive():
        assert one.is_pristine()
        with pytest.raises(hf.StoreError, match="hf_namespace_busy"):
            with two.exclusive():
                pytest.fail("second holder entered")
        assert one.read_head() is None
    with two.exclusive():
        assert two._state["epoch"] == 2


def test_concurrent_claims_cannot_both_enter():
    remote = Remote()
    barrier, finished = threading.Barrier(2), threading.Event()
    original = remote.snapshot
    first_calls = []
    mutex = threading.Lock()

    def snapshot():
        result = original()
        with mutex:
            first_calls.append(1)
            rendezvous = len(first_calls) <= 2
        if rendezvous:
            barrier.wait(timeout=5)
        return result

    remote.snapshot = snapshot

    def attempt(_):
        try:
            with store(remote).exclusive():
                assert finished.wait(5)
                return "entered"
        except hf.StoreError as exc:
            finished.set()
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["entered", "hf_head_conflict"]


def test_lost_claim_ack_leaves_fail_closed_lock():
    remote = Remote()
    remote.lose_ack = True
    with pytest.raises(OSError):
        with store(remote).exclusive():
            pytest.fail("uncertain acquisition entered")
    with pytest.raises(hf.StoreError, match="hf_namespace_busy"):
        with store(remote).exclusive():
            pytest.fail("new process stole an uncertain holder")


def test_fenced_holder_cannot_publish_or_release_new_revision():
    remote = Remote()
    held = store(remote)
    with pytest.raises(hf.StoreError, match="hf_lease_fenced"):
        with held.exclusive():
            remote.commit(remote.revision, {"unrelated.json": b"synthetic"})
            with pytest.raises(hf.StoreError, match="hf_lease_fenced"):
                held.compare_and_swap_head(None, b"new")
    control = held._decode(remote.read(remote.revision, held.control))
    assert control["owner"] is not None and control["head"] is None


def test_missing_control_never_reinitializes_populated_namespace():
    remote = Remote()
    held = store(remote)
    remote.commit(remote.revision, {held.prefix + "orphan.json": b"old synthetic archive"})
    count = remote.commits
    with pytest.raises(hf.StoreError, match="hf_namespace_not_pristine"):
        with held.exclusive():
            pytest.fail("reinitialized")
    assert remote.commits == count


def test_objects_are_digest_bound_immutable_and_head_is_conditional():
    held = store()
    raw = b"synthetic"
    key = "archives/" + hashlib.sha256(raw).hexdigest()
    with held.exclusive():
        held.put_immutable(key, raw)
        assert not held.is_pristine()
        held.put_immutable(key, raw)
        assert held.get(key) == raw
        with pytest.raises(hf.StoreError, match="hf_object_invalid"):
            held.put_immutable(key, b"other")
        count = held.remote.commits
        with pytest.raises(hf.StoreError, match="hf_object_invalid"):
            held.put_immutable("archives/" + hashlib.sha256(b"new").hexdigest(), raw)
        assert held.remote.commits == count
        assert not held.compare_and_swap_head(b"missing", b"new")
        assert held.read_head() is None
        assert held.compare_and_swap_head(None, b"new")
        assert held.read_head() == b"new"


def test_namespace_and_key_and_lifecycle_are_not_optional():
    with pytest.raises(hf.StoreError, match="hf_namespace_invalid"):
        hf.HubCheckpointStore(Remote(), "../other")
    held = store()
    with pytest.raises(hf.StoreError, match="hf_lease_required"):
        held.read_head()
    with held.exclusive():
        with pytest.raises(hf.StoreError, match="hf_key_invalid"):
            held.get("../../owner")
        with pytest.raises(hf.StoreError, match="hf_lease_already_held"):
            with held.exclusive():
                pytest.fail("nested lease")


def test_foreign_control_or_oversized_object_refused():
    held = store()
    bad = {"schema": 1, "namespace": "cd" * 16, "epoch": 1, "owner": None, "head": None}
    held.remote.commit(held.remote.revision, {held.control: canonical_bytes(bad)})
    with pytest.raises(hf.StoreError, match="hf_control_invalid"):
        with held.exclusive():
            pytest.fail("foreign control")
    other = store()
    raw = b"x" * (hf.MAX_OBJECT + 1)
    with other.exclusive():
        with pytest.raises(hf.StoreError, match="hf_object_invalid"):
            other.put_immutable("archives/" + hashlib.sha256(raw).hexdigest(), raw)


def test_http_transport_requires_private_target_and_atomic_parent(monkeypatch):
    with pytest.raises(hf.StoreError, match="hf_target_invalid"):
        hf.HubRemote("space", "synthetic/probe", "main", "synthetic-token")
    remote = hf.HubRemote("space", "synthetic/probe", "diwan-checkpoint-test", "synthetic-token")
    responses = []

    def request(url, *, body=None, **kwargs):
        if body is None:
            return canonical_bytes({"private": False, "sha": "0" * 40, "siblings": []})
        responses.extend(json.loads(line) for line in body.splitlines())
        return canonical_bytes({"commitOid": "1" * 40})

    monkeypatch.setattr(remote, "_request", request)
    with pytest.raises(hf.StoreError, match="hf_private_required"):
        remote.snapshot()
    assert remote.commit("0" * 40, {"synthetic.json": b"{}"}) == "1" * 40
    assert responses[0]["value"]["parentCommit"] == "0" * 40


def test_http_errors_and_redirects_do_not_expose_auth_or_retry():
    remote = hf.HubRemote("space", "synthetic/probe", "diwan-checkpoint-test", "synthetic-secret")
    calls = []

    class Failure:
        def open(self, request, *, timeout):
            calls.append(1)
            assert timeout == 30
            assert request.get_header("Authorization") == "Bearer synthetic-secret"
            raise urllib.error.HTTPError(request.full_url, 412, "private diagnostic", {}, None)

    remote._opener = Failure()
    with pytest.raises(hf.StoreError, match="^hf_head_conflict$"):
        remote.commit("0" * 40, {"synthetic.json": b"{}"})
    assert len(calls) == 1
    request = urllib.request.Request("https://huggingface.co/synthetic", headers={"Authorization": "secret"})
    with pytest.raises(hf.StoreError, match="hf_redirect_refused"):
        hf._NoRedirect().redirect_request(request, None, 302, "redirect", {}, "https://example.com/")


def test_local_only_rejected_before_real_adapter_transport(sample):
    path, sha, approval = exported(sample)
    for grant in approval["files"].values():
        grant["data_policy"] = "local_only"
    remote = Remote()
    with pytest.raises(cp.CheckpointError, match="checkpoint_policy_refused"):
        cp.commit_checkpoint(path, sha, approval, store(remote))
    assert remote.commits == 0


def test_private_visibility_is_rechecked_inside_an_acquired_lease(monkeypatch):
    memory_remote = Remote()
    remote = hf.HubRemote("space", "synthetic/probe", "diwan-checkpoint-test", "synthetic-token")
    visibility = {"private": True}

    def request(url, *, body=None, **kwargs):
        if "/revision/" in url:
            revision, paths = memory_remote.snapshot()
            return canonical_bytes({"private": visibility["private"], "sha": revision,
                                    "siblings": [{"rfilename": p} for p in paths]})
        if body is not None:
            parts = [json.loads(line) for line in body.splitlines()]
            files = {p["value"]["path"]: base64.b64decode(p["value"]["content"]) for p in parts[1:]}
            sha = memory_remote.commit(parts[0]["value"]["parentCommit"], files)
            return canonical_bytes({"commitOid": sha})
        revision, path = url.split("/raw/")[1].split("/", 1)
        return memory_remote.read(revision, path)

    monkeypatch.setattr(remote, "_request", request)
    held = hf.HubCheckpointStore(remote, NS)
    with pytest.raises(hf.StoreError, match="hf_private_required"):
        with held.exclusive():
            before = memory_remote.commits
            visibility["private"] = False
            with pytest.raises(hf.StoreError, match="hf_private_required"):
                held.compare_and_swap_head(None, b"must not leave client")
    assert memory_remote.commits == before


def test_malformed_metadata_fails_without_reading_any_file(monkeypatch):
    remote = hf.HubRemote("space", "synthetic/probe", "diwan-checkpoint-test", "synthetic-token")
    for value in ([], {"private": True, "sha": "not-a-commit", "siblings": []},
                  {"private": True, "sha": "0" * 40, "siblings": [{"rfilename": "a"}, {"rfilename": "a"}]},
                  {"private": True, "sha": "0" * 40}):
        monkeypatch.setattr(remote, "_request", lambda *args, **kwargs: canonical_bytes(value))
        with pytest.raises(hf.StoreError, match="hf_metadata_invalid"):
            remote.snapshot()


def test_response_read_is_bounded_before_decoding():
    remote = hf.HubRemote("space", "synthetic/probe", "diwan-checkpoint-test", "synthetic-token")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, count):
            assert count == 8
            return b"x" * 8

    class Opener:
        def open(self, request, *, timeout):
            return Response()

    remote._opener = Opener()
    with pytest.raises(hf.StoreError, match="hf_response_limit"):
        remote._request("https://huggingface.co/synthetic", limit=7)
