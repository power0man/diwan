"""Policy-aware rotation and complete, public-only historical verification."""
import hashlib
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.test_ed25519_signing import ed
from core.canonical import canonical_bytes
from core.ledger import Ledger, LedgerCorrupt
from core.seal import sealed_append
from core.snapshot import rotate, full_entries, snapshots_of
import core.signing as s
from tools import sign_anchors as cli
from tools import migrate_ed25519 as migrate


def test_rotation_is_ed_and_preserves_multiple_generations(ed, monkeypatch):
    _, seed, _, ledger = ed
    monkeypatch.setattr(s, "load_ed25519_private_key", lambda: seed)
    before = full_entries(ledger.path)
    first = rotate(ledger.path)
    sealed_append(ledger, {"kind": "next"}, "synthetic")
    before = before + [ledger.entries()[-1]]
    second = rotate(ledger.path)
    assert full_entries(ledger.path) == before
    for path in (ledger.path, first, second):
        generation = Ledger(path, create=False)
        assert s.parse_signature(s.sig_path(generation).read_bytes())[0] == s.ED25519
        assert s.verify_anchor_signature(generation)


def test_wrong_ed_rotation_identity_leaves_no_snapshot(ed, monkeypatch):
    _, _, _, ledger = ed
    before = (ledger.path.read_bytes(), ledger.anchor_path.read_bytes(), s.sig_path(ledger).read_bytes())
    monkeypatch.setattr(s, "load_ed25519_private_key", lambda: b"wrong"*6+b"xx")
    with pytest.raises(s.SigningRefused, match="signing_key_mismatch"):
        rotate(ledger.path)
    assert snapshots_of(ledger.path) == []
    assert (ledger.path.read_bytes(), ledger.anchor_path.read_bytes(), s.sig_path(ledger).read_bytes()) == before


@pytest.fixture
def rotated(ed, monkeypatch):
    root, seed, public, ledger = ed
    monkeypatch.setattr(s, "load_ed25519_private_key", lambda: seed)
    snap = rotate(ledger.path)
    monkeypatch.setattr(cli, "SIGNED_LEDGERS", frozenset({"sources/acquisitions.jsonl"}))
    def forbidden(): pytest.fail("public history verification loaded a secret")
    monkeypatch.setattr(s, "load_ed25519_private_key", forbidden)
    monkeypatch.setattr(s, "load_key", forbidden)
    assert cli.verify_repository(root, public_key=public) == {"verified": 2, "skipped": 0, "failures": []}
    return root, seed, public, ledger, snap


@pytest.mark.parametrize("artifact", ["ledger", "anchor", "signature"])
def test_public_gate_rejects_missing_historical_artifacts(rotated, artifact):
    root, _, public, _, snap = rotated
    generation = Ledger(snap, create=False)
    {"ledger": snap, "anchor": generation.anchor_path, "signature": s.sig_path(generation)}[artifact].unlink()
    assert cli.verify_repository(root, public_key=public)["failures"]
    assert not {"ledger": snap, "anchor": generation.anchor_path, "signature": s.sig_path(generation)}[artifact].exists()


@pytest.mark.parametrize("mutation", ["hmac", "altered", "symlink", "fifo"])
def test_public_gate_rejects_invalid_historical_generation(rotated, mutation):
    root, _, public, _, snap = rotated
    generation = Ledger(snap, create=False)
    if mutation == "hmac": s.sig_path(generation).write_bytes(s.make_signature(generation, b"historical"))
    elif mutation == "altered": snap.write_bytes(snap.read_bytes()+b" ")
    elif mutation == "symlink":
        renamed = snap.with_suffix(".other")
        snap.rename(renamed)
        snap.symlink_to(renamed)
    else:
        import os
        snap.unlink()
        os.mkfifo(snap)
    assert cli.verify_repository(root, public_key=public)["failures"]


def test_explicit_candidate_root_history_does_not_use_global_policy_root(rotated, monkeypatch):
    root, _, public, _, _ = rotated
    monkeypatch.setattr(s, "ROOT", root / "different-trusted-verifier-location")
    assert cli.verify_repository(root, public_key=public)["failures"] == []


@pytest.mark.parametrize("name", ["../outside.jsonl", "/tmp/outside.jsonl", "unrelated.snap-1.jsonl"])
def test_signed_link_cannot_escape_snapshot_family(rotated, name):
    root, seed, public, ledger, _ = rotated
    link = ledger.entries()[0]["record"]
    link["snapshot"] = name
    ledger.path.write_bytes(b"")
    ledger.append(link)
    ledger.anchor()
    s.sign_anchor(ledger, seed)
    assert cli.verify_repository(root, public_key=public)["failures"]


@pytest.fixture
def historical(tmp_path, monkeypatch):
    root = tmp_path
    monkeypatch.setattr(s, "ROOT", root)
    monkeypatch.setattr(migrate, "ROOT", root)
    monkeypatch.setattr(s, "PINNED_POLICY_SHA256", None)
    monkeypatch.setattr(s, "SIGNED_LEDGERS", frozenset({"ledger/main.jsonl"}))
    monkeypatch.setattr(cli, "SIGNED_LEDGERS", frozenset({"ledger/main.jsonl"}))
    monkeypatch.setattr(s, "load_key", lambda: b"synthetic HMAC")
    ledger = Ledger(root / "ledger/main.jsonl")
    ledger.append({"kind": "one"})
    ledger.anchor()
    s.sign_anchor(ledger, b"synthetic HMAC")
    rotate(ledger.path)
    sealed_append(ledger, {"kind": "two"}, "synthetic")
    rotate(ledger.path)
    seed = Ed25519PrivateKey.generate().private_bytes_raw()
    public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    monkeypatch.setattr(s, "load_ed25519_private_key", lambda: seed)
    return root, ledger, public


def test_migration_enrolls_all_hmac_generations_before_ed_activation(historical, monkeypatch):
    root, ledger, public = historical
    expected = full_entries(ledger.path)
    data = {p: p.read_bytes() for p in (ledger.path, *snapshots_of(ledger.path))}
    obj = migrate.plan(public, hashlib.sha256(public).hexdigest())
    assert len(obj["entries"]) == 3
    raw = canonical_bytes(obj)
    assert migrate.apply(raw, hashlib.sha256(raw).hexdigest(), resign=True)["converted"] == 3
    assert all(p.read_bytes() == value for p, value in data.items())
    (root / "keys").mkdir()
    (root / s.PUBLIC_KEY_PATH).write_bytes(public)
    policy = s.canonical_policy_bytes(s.policy_document(public))
    (root / s.POLICY_PATH).write_bytes(policy)
    monkeypatch.setattr(s, "PINNED_POLICY_SHA256", hashlib.sha256(policy).hexdigest())
    def forbidden(): pytest.fail("post-migration history loaded secret")
    monkeypatch.setattr(s, "load_key", forbidden)
    monkeypatch.setattr(s, "load_ed25519_private_key", forbidden)
    assert full_entries(ledger.path) == expected
    assert cli.verify_repository(root, public_key=public) == {"verified": 3, "skipped": 0, "failures": []}


def test_invalid_historical_hmac_stops_migration_before_any_replacement(historical):
    root, ledger, public = historical
    snap = Ledger(snapshots_of(ledger.path)[0], create=False)
    s.sig_path(snap).write_bytes(b"HMAC-SHA256:" + b"0"*64 + b"\n")
    obj = migrate.plan(public, hashlib.sha256(public).hexdigest())
    raw = canonical_bytes(obj)
    before = {s.sig_path(Ledger(p, create=False)): s.sig_path(Ledger(p, create=False)).read_bytes()
              for p in (ledger.path, *snapshots_of(ledger.path))}
    with pytest.raises(s.SigningRefused, match="signature_mismatch"):
        migrate.apply(raw, hashlib.sha256(raw).hexdigest(), resign=True)
    assert all(p.read_bytes() == value for p, value in before.items())
    assert not (root / "var").exists()


def test_missing_historical_snapshot_refuses_plan_without_repair(historical):
    _, ledger, public = historical
    missing = snapshots_of(ledger.path)[0]
    missing.unlink()
    with pytest.raises((LedgerCorrupt, s.SigningRefused)):
        migrate.plan(public, hashlib.sha256(public).hexdigest())
    assert not missing.exists()
