"""Conversion must preserve data and refuse every unapproved state transition."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import signing
from core.canonical import canonical_bytes
from core.ledger import Ledger
from tools import anchor_keys, migrate_ed25519 as migration


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def migration_case(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "ROOT", tmp_path)
    monkeypatch.setattr(signing, "ROOT", tmp_path)
    monkeypatch.setattr(signing, "PINNED_POLICY_SHA256", None)
    scopes = frozenset({"sources/acquisitions.jsonl", "registry/nodes.jsonl"})
    monkeypatch.setattr(signing, "SIGNED_LEDGERS", scopes)
    monkeypatch.setattr(signing, "discovered_stream_scopes", lambda root: frozenset())
    old_key = b"synthetic-old-hmac-key"
    key = Ed25519PrivateKey.generate()
    seed, public = key.private_bytes_raw(), key.public_key().public_bytes_raw()
    monkeypatch.setattr(signing, "load_key", lambda: old_key)
    monkeypatch.setattr(signing, "load_ed25519_private_key", lambda: seed)
    ledgers = []
    for scope in sorted(scopes):
        led = Ledger(tmp_path / scope)
        led.append({"synthetic": scope})
        led.anchor()
        signing.sig_path(led).write_bytes(signing.make_signature(
            led, old_key, algorithm=signing.ALG, root=tmp_path))
        ledgers.append(led)
    def prepare():
        raw = canonical_bytes(migration.plan(public, sha(public)))
        return raw, sha(raw)
    return tmp_path, ledgers, seed, public, prepare


def data_snapshot(ledgers):
    return {str(path): path.read_bytes() for led in ledgers
            for path in (led.path, led.anchor_path)}


def signature_snapshot(ledgers):
    return [signing.sig_path(led).read_bytes() for led in ledgers]


def test_plan_is_read_only_and_conversion_preserves_data(migration_case):
    root, ledgers, _, public, prepare = migration_case
    before, old_sigs = data_snapshot(ledgers), signature_snapshot(ledgers)
    raw, digest = prepare()
    assert not (root / "var").exists()
    assert signature_snapshot(ledgers) == old_sigs
    result = migration.apply(raw, digest, resign=True)
    assert result["converted"] == 2
    assert data_snapshot(ledgers) == before
    for led in ledgers:
        message = signing.signature_message(led, led.anchor_path.read_bytes(),
                                            algorithm=signing.ED25519, root=root)
        assert signing.verify_signature_bytes(signing.sig_path(led).read_bytes(), message,
                                              public_key=public)
    backups = sorted((root / "var/signature-migration" / digest).glob("*.sig"))
    assert sorted(p.read_bytes() for p in backups) == sorted(old_sigs)
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in backups)


def test_explicit_conversion_required(migration_case, monkeypatch):
    _, ledgers, _, _, prepare = migration_case
    raw, digest = prepare()
    monkeypatch.setattr(signing, "load_key", lambda: pytest.fail("secret read"))
    with pytest.raises(signing.SigningRefused, match="migration_conversion_approval_required"):
        migration.apply(raw, digest)


def test_invalid_last_hmac_refuses_before_first_replacement(migration_case):
    root, ledgers, _, _, prepare = migration_case
    signing.sig_path(ledgers[-1]).write_bytes(b"HMAC-SHA256:" + b"0" * 64 + b"\n")
    raw, digest = prepare()
    before = signature_snapshot(ledgers)
    with pytest.raises(signing.SigningRefused):
        migration.apply(raw, digest, resign=True)
    assert signature_snapshot(ledgers) == before
    assert not (root / "var").exists()


def test_stale_data_refused_before_key_lookup(migration_case, monkeypatch):
    _, ledgers, _, _, prepare = migration_case
    raw, digest = prepare()
    ledgers[-1].append({"new": True})
    ledgers[-1].anchor()
    monkeypatch.setattr(signing, "load_key", lambda: pytest.fail("secret read"))
    with pytest.raises(signing.SigningRefused, match="migration_state_changed"):
        migration.apply(raw, digest, resign=True)


def test_changed_signature_refused_before_key_lookup(migration_case, monkeypatch):
    _, ledgers, _, _, prepare = migration_case
    raw, digest = prepare()
    signing.sig_path(ledgers[0]).write_bytes(b"HMAC-SHA256:" + b"0" * 64 + b"\n")
    monkeypatch.setattr(signing, "load_key", lambda: pytest.fail("secret read"))
    with pytest.raises(signing.SigningRefused, match="migration_signature_changed"):
        migration.apply(raw, digest, resign=True)


def test_wrong_new_private_key_does_not_change_signatures(migration_case, monkeypatch):
    root, ledgers, _, _, prepare = migration_case
    raw, digest = prepare()
    monkeypatch.setattr(signing, "load_ed25519_private_key", lambda: b"z" * 32)
    before = signature_snapshot(ledgers)
    with pytest.raises(signing.SigningRefused):
        migration.apply(raw, digest, resign=True)
    assert signature_snapshot(ledgers) == before
    assert not (root / "var").exists()


def test_interrupted_conversion_resumes_exact_plan(migration_case, monkeypatch):
    _, ledgers, _, _, prepare = migration_case
    raw, digest = prepare()
    real = signing.atomic_write_signature
    calls = []
    def interrupted(path, blob):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("synthetic interruption")
        real(path, blob)
    monkeypatch.setattr(signing, "atomic_write_signature", interrupted)
    with pytest.raises(OSError):
        migration.apply(raw, digest, resign=True)
    assert signing.sig_path(ledgers[0]).read_bytes().startswith(b"ED25519:")
    assert signing.sig_path(ledgers[1]).read_bytes().startswith(b"HMAC-SHA256:")
    monkeypatch.setattr(signing, "atomic_write_signature", real)
    result = migration.apply(raw, digest, resign=True)
    assert result["converted"] == 1 and result["already_verified"] == 1


def test_completed_resume_never_loads_a_secret(migration_case, monkeypatch):
    _, ledgers, _, _, prepare = migration_case
    raw, digest = prepare()
    migration.apply(raw, digest, resign=True)
    before = signature_snapshot(ledgers)
    monkeypatch.setattr(signing, "load_key", lambda: pytest.fail("legacy secret read"))
    monkeypatch.setattr(signing, "load_ed25519_private_key", lambda: pytest.fail("private secret read"))
    result = migration.apply(raw, digest, resign=True)
    assert result["already_verified"] == 2 and result["converted"] == 0
    assert signature_snapshot(ledgers) == before


def test_other_ed_key_is_not_treated_as_resume(migration_case):
    root, ledgers, _, _, prepare = migration_case
    raw, digest = prepare()
    stranger = Ed25519PrivateKey.generate()
    signing.sig_path(ledgers[0]).write_bytes(signing.make_signature(
        ledgers[0], stranger.private_bytes_raw(), algorithm=signing.ED25519,
        public_key=stranger.public_key().public_bytes_raw(), root=root))
    with pytest.raises(signing.SigningRefused):
        migration.apply(raw, digest, resign=True)


def test_incomplete_plan_is_rejected(migration_case, monkeypatch):
    _, _, _, _, prepare = migration_case
    raw, _ = prepare()
    obj = json.loads(raw)
    obj["entries"].pop()
    raw = canonical_bytes(obj)
    monkeypatch.setattr(signing, "load_key", lambda: pytest.fail("secret read"))
    with pytest.raises(signing.SigningRefused, match="migration_scope_changed"):
        migration.apply(raw, sha(raw), resign=True)


def test_plan_cannot_relabel_hmac_to_skip_preservation(migration_case, monkeypatch):
    root, ledgers, _, _, prepare = migration_case
    raw, _ = prepare()
    obj = json.loads(raw)
    obj["entries"][0]["algorithm"] = signing.ED25519
    raw = canonical_bytes(obj)
    before = signature_snapshot(ledgers)
    monkeypatch.setattr(signing, "load_key", lambda: pytest.fail("secret read"))
    with pytest.raises(signing.SigningRefused, match="migration_plan_algorithm_mismatch"):
        migration.apply(raw, sha(raw), resign=True)
    assert signature_snapshot(ledgers) == before
    assert not (root / "var").exists()


@pytest.mark.parametrize("change", ["root", "count", "extra", "digest"])
def test_corrupt_plan_is_rejected(migration_case, change):
    _, _, _, _, prepare = migration_case
    raw, digest = prepare()
    obj = json.loads(raw)
    if change == "root": obj["root_sha256"] = "0" * 64
    elif change == "count": obj["entries"][0]["count"] = True
    elif change == "extra": obj["extra"] = "no"
    if change != "digest": raw = canonical_bytes(obj); digest = sha(raw)
    else: digest = "0" * 64
    with pytest.raises(signing.SigningRefused):
        migration.apply(raw, digest, resign=True)


def test_symlink_signature_is_rejected(migration_case):
    root, ledgers, _, _, prepare = migration_case
    raw, digest = prepare()
    p = signing.sig_path(ledgers[0]); other = root / "elsewhere.sig"
    p.rename(other); p.symlink_to(other)
    with pytest.raises(signing.SigningRefused):
        migration.apply(raw, digest, resign=True)


def test_public_backup_directory_is_rejected(migration_case):
    root, ledgers, _, _, prepare = migration_case
    raw, digest = prepare()
    (root / "var").mkdir(mode=0o755)
    (root / "var/signature-migration").mkdir(mode=0o755)
    before = signature_snapshot(ledgers)
    with pytest.raises(signing.SigningRefused, match="migration_backup_not_private"):
        migration.apply(raw, digest, resign=True)
    assert signature_snapshot(ledgers) == before


def test_existing_public_var_parent_does_not_need_chmod(migration_case):
    root, _, _, _, prepare = migration_case
    (root / "var").mkdir(mode=0o755)
    raw, digest = prepare()
    assert migration.apply(raw, digest, resign=True)["converted"] == 2
    assert (root / "var").stat().st_mode & 0o777 == 0o755
    assert (root / "var/signature-migration").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("tamper", ["missing", "changed", "public"])
def test_resume_requires_preserved_old_signature(migration_case, tamper):
    root, ledgers, _, _, prepare = migration_case
    raw, digest = prepare()
    migration.apply(raw, digest, resign=True)
    backup = next((root / "var/signature-migration" / digest).glob("*.sig"))
    if tamper == "missing": backup.unlink()
    elif tamper == "changed": backup.write_bytes(b"HMAC-SHA256:" + b"0" * 64 + b"\n")
    else: backup.chmod(0o644)
    before = signature_snapshot(ledgers)
    with pytest.raises(signing.SigningRefused, match="migration_backup_"):
        migration.apply(raw, digest, resign=True)
    assert signature_snapshot(ledgers) == before


def test_key_creation_never_writes_private_material(tmp_path, monkeypatch, capsys):
    saved = []
    monkeypatch.setattr(anchor_keys, "_store_private_key", saved.append)
    path = tmp_path / "public.raw"
    assert anchor_keys.main(["create", "--public-output", str(path)]) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert len(saved) == 1 and len(saved[0]) == 32
    assert saved[0].hex() not in output
    assert path.read_bytes() == Ed25519PrivateKey.from_private_bytes(saved[0]).public_key().public_bytes_raw()
    assert result["public_key_sha256"] == sha(path.read_bytes())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["public.raw"]


def test_existing_public_path_prevents_key_creation(tmp_path, monkeypatch):
    path = tmp_path / "public.raw"; path.write_bytes(b"existing")
    monkeypatch.setattr(anchor_keys, "_store_private_key", lambda _: pytest.fail("key mutation"))
    with pytest.raises(anchor_keys.KeySetupRefused, match="public_output_exists"):
        anchor_keys.create(path)
    assert path.read_bytes() == b"existing"


def test_key_export_recovers_after_publication_failure(tmp_path, monkeypatch):
    seed = b"s" * 32
    monkeypatch.setattr(signing, "load_ed25519_private_key", lambda: seed)
    result = anchor_keys.export(tmp_path / "recovered.pub")
    assert result["status"] == "exported"
    assert (tmp_path / "recovered.pub").read_bytes() == Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
