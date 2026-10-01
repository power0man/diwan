"""Branch-wide ownership survives a cold client; only synthetic contracts/data."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading

import pytest

from core.canonical import canonical_bytes, digest
from workspace_tools import hf_checkpoints as hf
from workspace_tools.storage_scope import plan_cloud_workspace
from tests.test_hf_checkpoints import Remote, NS


class ScopedRemote(Remote):
    repo_type = "space"
    repo_id = "synthetic/closed-probe"
    branch = "diwan-checkpoint-bound-test"


def scope_raw(*, namespace=NS, **changes):
    plan = plan_cloud_workspace({"provider": "hf_hub", "repo_type": ScopedRemote.repo_type,
                                "repo_id": ScopedRemote.repo_id, "branch": ScopedRemote.branch})
    plan["target"]["namespace"] = namespace
    plan.update(changes)
    record = {"plan": plan, "approval": {"owner_approved": True, "plan_sha256": digest(plan)}}
    return canonical_bytes({"record": record, "sha256": digest(record)})


def bound():
    remote, raw = ScopedRemote(), scope_raw()
    held = hf.HubCheckpointStore(remote, NS)
    with held.exclusive():
        held.bind_scope(raw)
    return remote, raw


def test_cold_client_must_match_durable_contract_before_reusing_data():
    remote, raw = ScopedRemote(), scope_raw()
    first = hf.HubCheckpointStore(remote, NS)
    payload = b"synthetic public checkpoint bytes"
    key = "archives/" + hashlib.sha256(payload).hexdigest()
    with first.exclusive():
        identity = first.bind_scope(raw)
        assert identity == {"workspace_id": json.loads(raw)["record"]["plan"]["workspace_id"],
                            "contract_sha256": json.loads(raw)["sha256"],
                            "target": json.loads(raw)["record"]["plan"]["target"]}
        assert first.is_pristine()
        first.put_immutable(key, payload)
        assert first.compare_and_swap_head(None, b"synthetic head")
    assert remote.read(remote.revision, hf.SCOPE_FILE) == raw
    del first  # No local creation authority is available to the reopened client.
    reopened = hf.HubCheckpointStore(remote, NS)
    with reopened.exclusive():
        with pytest.raises(hf.StoreError, match="hf_scope_verification_required"):
            reopened.read_head()
        assert reopened.bind_scope(raw) == identity  # Idempotent verification, not adoption.
        assert reopened.verify_scope(raw) == identity
        assert reopened.get(key) == payload and reopened.read_head() == b"synthetic head"
    with reopened.exclusive():
        with pytest.raises(hf.StoreError, match="hf_scope_verification_required"):
            reopened.read_head()  # Verification cannot leak across lease lifetimes.


def test_every_data_method_requires_verification_in_a_bound_lease():
    remote, raw = bound()
    held = hf.HubCheckpointStore(remote, NS)
    payload = b"synthetic"
    key = "archives/" + hashlib.sha256(payload).hexdigest()
    with held.exclusive():
        before = remote.commits
        for call in (held.read_head, held.is_pristine, lambda: held.get(key),
                     lambda: held.put_immutable(key, payload),
                     lambda: held.compare_and_swap_head(None, b"new")):
            with pytest.raises(hf.StoreError, match="hf_scope_verification_required"):
                call()
        assert remote.commits == before
        held.verify_scope(raw)
        assert held.read_head() is None


def test_second_namespace_is_refused_before_claim_even_with_a_fresh_contract():
    remote, _ = bound()
    before = remote.commits
    held = hf.HubCheckpointStore(remote, "cd" * 16)
    with pytest.raises(hf.StoreError, match="hf_scope_target_mismatch"):
        with held.exclusive():
            held.bind_scope(scope_raw(namespace="cd" * 16))
    assert remote.commits == before


@pytest.mark.parametrize("reservation", ["same_namespace", "other_namespace", "orphan"])
def test_first_binding_never_adopts_an_old_claim_or_other_namespace(reservation):
    remote = ScopedRemote()
    if reservation == "orphan":
        remote.commit(remote.revision, {"checkpoints/elsewhere/archive.json": b"synthetic"})
    else:
        old = hf.HubCheckpointStore(remote, NS if reservation == "same_namespace" else "cd" * 16)
        with old.exclusive():
            pass
    held = hf.HubCheckpointStore(remote, NS)
    with held.exclusive():
        before = remote.commits
        with pytest.raises(hf.StoreError, match="hf_branch_not_pristine"):
            held.bind_scope(scope_raw())
        assert remote.commits == before
    assert hf.SCOPE_FILE not in remote.snapshot()[1]


def test_a_different_workspace_or_contract_cannot_rebind_the_same_target():
    remote, raw = bound()
    held = hf.HubCheckpointStore(remote, NS)
    with held.exclusive():
        before = remote.commits
        for call in (held.bind_scope, held.verify_scope):
            with pytest.raises(hf.StoreError, match="hf_scope_mismatch"):
                call(scope_raw())
        assert remote.commits == before
        assert remote.read(remote.revision, hf.SCOPE_FILE) == raw


@pytest.mark.parametrize("field,value", [("repo_type", "dataset"), ("repo_id", "synthetic/other"),
                                           ("branch", "diwan-checkpoint-other"), ("namespace", "cd" * 16)])
def test_expected_target_must_match_actual_transport(field, value):
    remote = ScopedRemote()
    plan = json.loads(scope_raw())["record"]["plan"]
    plan["target"][field] = value
    raw = scope_raw(**plan)
    held = hf.HubCheckpointStore(remote, NS)
    with pytest.raises(hf.StoreError, match="hf_scope_target_mismatch"):
        held.bind_scope(raw)
    assert remote.commits == 0


def test_binding_rejects_unapproved_contracts_and_requires_the_lease():
    held = hf.HubCheckpointStore(ScopedRemote(), NS)
    raw = scope_raw()
    bad = json.loads(raw)
    bad["record"]["approval"]["owner_approved"] = False
    for payload in (b"{}", canonical_bytes(bad), raw + b"\n"):
        with pytest.raises(hf.StoreError, match="hf_scope_invalid"):
            held.bind_scope(payload)
    for call in (held.bind_scope, held.verify_scope):
        with pytest.raises(hf.StoreError, match="hf_lease_required"):
            call(raw)
    assert held.remote.commits == 0


@pytest.mark.parametrize("damage,code", [("missing_binding", "hf_scope_binding_missing"),
                                        ("missing_control", "hf_scope_binding_missing"),
                                        ("changed_binding", "hf_scope_binding_corrupt"),
                                        ("invalid_pin", "hf_control_invalid")])
def test_lost_or_changed_binding_cannot_downgrade_to_legacy(damage, code):
    remote, raw = bound()
    held = hf.HubCheckpointStore(remote, NS)
    files = remote.versions[remote.revision]
    if damage == "missing_binding":
        del files[hf.SCOPE_FILE]
    elif damage == "missing_control":
        del files[held.control]
    elif damage == "changed_binding":
        files[hf.SCOPE_FILE] = scope_raw()
    else:
        state = json.loads(files[held.control]);state["scope_sha256"] = "bad"
        files[held.control] = canonical_bytes(state)
    before = remote.commits
    with pytest.raises(hf.StoreError, match=code):
        with held.exclusive():
            pytest.fail("damaged binding entered")
    assert remote.commits == before


def test_verify_never_installs_a_missing_binding():
    held = hf.HubCheckpointStore(ScopedRemote(), NS)
    with held.exclusive():
        before = held.remote.commits
        with pytest.raises(hf.StoreError, match="hf_scope_unbound"):
            held.verify_scope(scope_raw())
        assert held.remote.commits == before
    assert hf.SCOPE_FILE not in held.remote.snapshot()[1]


def test_lost_binding_ack_remains_busy_without_automatic_recovery():
    remote = ScopedRemote()
    held = hf.HubCheckpointStore(remote, NS)
    with pytest.raises(hf.StoreError, match="hf_lease_fenced"):
        with held.exclusive():
            remote.lose_ack = True
            held.bind_scope(scope_raw())
    assert hf.SCOPE_FILE in remote.snapshot()[1]
    before = remote.commits
    with pytest.raises(hf.StoreError, match="hf_namespace_busy"):
        with hf.HubCheckpointStore(remote, NS).exclusive():
            pytest.fail("uncertain lease stolen")
    assert remote.commits == before


def test_competing_new_namespaces_cannot_both_bind_the_branch():
    remote = ScopedRemote()
    barrier, finished, mutex = threading.Barrier(2), threading.Event(), threading.Lock()
    original = remote.snapshot
    calls = []

    def snapshot():
        result = original()
        with mutex:
            calls.append(1); rendezvous = len(calls) <= 2
        if rendezvous:
            barrier.wait(timeout=5)
        return result

    remote.snapshot = snapshot

    def bind(namespace):
        held = hf.HubCheckpointStore(remote, namespace)
        try:
            with held.exclusive():
                held.bind_scope(scope_raw(namespace=namespace))
                assert finished.wait(5)
                return "bound"
        except hf.StoreError as exc:
            finished.set()
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(bind, (NS, "cd" * 16))) == ["bound", "hf_head_conflict"]
    files = remote.snapshot()[1]
    assert len([p for p in files if p.endswith("/control.json")]) == 1
    assert hf.SCOPE_FILE in files


def test_first_binding_refuses_an_already_published_head():
    held = hf.HubCheckpointStore(ScopedRemote(), NS)
    with held.exclusive():
        assert held.compare_and_swap_head(None, b"old public prototype head")
        before = held.remote.commits
        with pytest.raises(hf.StoreError, match="hf_branch_not_pristine"):
            held.bind_scope(scope_raw())
        assert held.remote.commits == before
        assert hf.SCOPE_FILE not in held.remote.snapshot()[1]


def test_restore_lease_never_claims_an_unbound_branch():
    remote = ScopedRemote()
    held = hf.HubCheckpointStore(remote, NS)
    for old_claim in (False, True):
        if old_claim:
            with held.exclusive():
                pass
        before = remote.commits
        with pytest.raises(hf.StoreError, match="hf_scope_unbound"):
            with held.exclusive(require_bound=True):
                pytest.fail("restore entered an unbound branch")
        assert remote.commits == before
    remote, raw = bound()
    held = hf.HubCheckpointStore(remote, NS)
    with held.exclusive(require_bound=True):
        held.verify_scope(raw)
        assert held.read_head() is None
