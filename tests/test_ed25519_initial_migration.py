"""First signatures after Ed activation, using only a synthetic key pair."""
import hashlib

import pytest
from tests.test_ed25519_signing import ed
from core.canonical import canonical_bytes
from core.ledger import Ledger
from core.seal import sealed_append
import core.signing as s
from tools import migrate_signatures as first


@pytest.fixture
def pending(ed, monkeypatch):
    root, seed, public, _ = ed
    monkeypatch.setattr(first, "ROOT", root)
    monkeypatch.setattr(first, "INITIAL_SIGNATURE_MIGRATION", frozenset())
    ledgers = [Ledger(root / f"streams/new/{box}.jsonl") for box in ("inbox", "outbox")]
    # No legacy or Ed private getter is permitted until a test explicitly
    # injects the synthetic Ed seed; the ed fixture poisons all production ones.
    return root, seed, public, ledgers


def planned():
    raw = canonical_bytes(first.plan(initialize_empty_streams=True))
    return raw, hashlib.sha256(raw).hexdigest()


def test_first_signatures_follow_ed_policy_then_support_normal_append(pending, monkeypatch):
    _, seed, _, ledgers = pending
    raw, digest = planned()
    calls = []
    def load(): calls.append(1); return seed
    monkeypatch.setattr(s, "load_ed25519_private_key", load)
    result = first.apply(raw, digest)
    assert result["signed"] == result["empty_anchors_created"] == 2
    assert len(calls) == 1
    for ledger in ledgers:
        assert s.parse_signature(s.sig_path(ledger).read_bytes())[0] == s.ED25519
        assert s.verify_anchor_signature(ledger)
        sealed_append(ledger, {"kind": "synthetic"}, "new stream")
        assert s.verify_anchor_signature(ledger)


@pytest.mark.parametrize("key", [b"bad", b"x"*32])
def test_private_identity_failure_precedes_all_anchor_creation(pending, monkeypatch, key):
    _, _, _, ledgers = pending
    raw, digest = planned()
    monkeypatch.setattr(s, "load_ed25519_private_key", lambda: key)
    with pytest.raises(s.SigningRefused, match="signing_key_(invalid|mismatch)"):
        first.apply(raw, digest)
    assert all(ledger.path.read_bytes() == b"" and not ledger.anchor_path.exists()
               and not s.sig_path(ledger).exists() for ledger in ledgers)


def test_completed_initial_migration_resume_is_public_only(pending, monkeypatch):
    _, seed, _, _ = pending
    raw, digest = planned()
    monkeypatch.setattr(s, "load_ed25519_private_key", lambda: seed)
    assert first.apply(raw, digest)["signed"] == 2
    monkeypatch.setattr(s, "load_ed25519_private_key", lambda: pytest.fail("resume private lookup"))
    assert first.apply(raw, digest)["already_verified"] == 2


def test_interruption_after_genesis_before_signature_is_resumable(pending, monkeypatch):
    _, seed, _, ledgers = pending
    raw, digest = planned()
    monkeypatch.setattr(s, "load_ed25519_private_key", lambda: seed)
    install = first._install_bytes
    def interruption(path, blob):
        if path.name.endswith(".sig"):
            raise OSError("synthetic interruption after anchor")
        return install(path, blob)
    monkeypatch.setattr(first, "_install_bytes", interruption)
    with pytest.raises(OSError): first.apply(raw, digest)
    assert ledgers[0].anchor_path.exists() and not s.sig_path(ledgers[0]).exists()
    monkeypatch.setattr(first, "_install_bytes", install)
    assert first.apply(raw, digest)["signed"] == 2
    assert all(s.verify_anchor_signature(ledger) for ledger in ledgers)
