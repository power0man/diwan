"""Synthetic keys only: public verification, policy pin, renewal and publication."""
import hashlib
import os
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.ledger import Ledger, LedgerCorrupt
from core.seal import require_seal, sealed_append
import core.signing as s
from tools import sign_anchors as cli


def forbidden(*args, **kwargs):
    pytest.fail("public verification attempted a secret lookup")


@pytest.fixture
def ed(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    seed = Ed25519PrivateKey.generate().private_bytes_raw()
    public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    raw_policy = s.canonical_policy_bytes(s.policy_document(public))
    (tmp_path / "keys").mkdir()
    (tmp_path / s.PUBLIC_KEY_PATH).write_bytes(public)
    (tmp_path / s.POLICY_PATH).write_bytes(raw_policy)
    monkeypatch.setattr(s, "PINNED_POLICY_SHA256", hashlib.sha256(raw_policy).hexdigest())
    monkeypatch.setattr(s, "load_key", forbidden)
    monkeypatch.setattr(s, "_keychain_key", forbidden)
    monkeypatch.setattr(s, "load_ed25519_private_key", forbidden)
    ledger = Ledger(tmp_path / "sources/acquisitions.jsonl")
    ledger.append({"kind": "synthetic"})
    ledger.anchor()
    s.sign_anchor(ledger, seed)
    return tmp_path, seed, public, ledger


def contents(ledger):
    return tuple(p.read_bytes() for p in (ledger.path, ledger.anchor_path, s.sig_path(ledger)))


def test_public_verify_never_loads_secret_even_when_given_legacy_key(ed, monkeypatch):
    _, _, _, ledger = ed
    monkeypatch.setenv(s.ENV_KEY, "ignored-legacy-environment")
    assert s.verify_anchor_signature(ledger, b"not an Ed key")
    assert require_seal(ledger, "fixture") == s.VERIFIED
    monkeypatch.setattr(cli, "GOVERNING", ("sources/acquisitions.jsonl",))
    monkeypatch.setattr(sys, "argv", ["sign_anchors.py", "verify", "--require-signature"])
    assert cli.main() == 0


@pytest.mark.parametrize("corrupt", ["anchor", "signature", "scope"])
def test_ed_tamper_and_scope_transfer_are_rejected(ed, corrupt):
    root, _, _, ledger = ed
    if corrupt == "anchor":
        ledger.anchor_path.write_bytes(ledger.anchor_path.read_bytes() + b" ")
    elif corrupt == "signature":
        raw = s.sig_path(ledger).read_bytes()
        s.sig_path(ledger).write_bytes(raw[:-3] + (b"00" if raw[-3:-1] != b"00" else b"01") + b"\n")
    else:
        other = Ledger(root / "registry/nodes.jsonl")
        other.path.write_bytes(ledger.path.read_bytes())
        other.anchor_path.write_bytes(ledger.anchor_path.read_bytes())
        s.sig_path(other).write_bytes(s.sig_path(ledger).read_bytes())
        ledger = other
    with pytest.raises(s.SigningRefused, match="signature_mismatch"):
        s.verify_anchor_signature(ledger)


@pytest.mark.parametrize("suffix", [b"", b"\r\n", b"\n\n", b" \n", b"\ntrailer", b"\x00\n"])
def test_parser_rejects_noncanonical_suffix(ed, suffix):
    _, _, _, ledger = ed
    raw = s.sig_path(ledger).read_bytes().removesuffix(b"\n") + suffix
    with pytest.raises(LedgerCorrupt):
        s.parse_signature(raw)


@pytest.mark.parametrize("raw", [b"ED25519:" + b"A"*128 + b"\n", b"ed25519:" + b"a"*128 + b"\n",
    b"ED25519:"+b"a"*127+b"\n", b"ED25519:"+b"a"*129+b"\n", b"X:"+b"a"*64+b"\n",
    b"HMAC-SHA256:"+b"0"*63+b"\n", b"\xff\n"])
def test_parser_rejects_unknown_and_malformed(raw):
    with pytest.raises(LedgerCorrupt):
        s.parse_signature(raw)


@pytest.mark.parametrize("mutation", ["delete_policy", "replace_policy", "delete_public", "replace_public", "length", "downgrade", "delete_sig"])
def test_approved_policy_refuses_downgrade_even_without_strict_env(ed, mutation):
    root, _, _, ledger = ed
    if mutation == "delete_policy": (root / s.POLICY_PATH).unlink()
    elif mutation == "replace_policy": (root / s.POLICY_PATH).write_bytes(b"{}\n")
    elif mutation == "delete_public": (root / s.PUBLIC_KEY_PATH).unlink()
    elif mutation == "replace_public": (root / s.PUBLIC_KEY_PATH).write_bytes(b"x"*32)
    elif mutation == "length": (root / s.PUBLIC_KEY_PATH).write_bytes(b"x"*31)
    elif mutation == "downgrade": s.sig_path(ledger).write_bytes(s.make_signature(ledger, b"old", algorithm=s.ALG))
    else: s.sig_path(ledger).unlink()
    with pytest.raises(s.SigningRefused):
        require_seal(ledger, "fixture")


def test_existing_unpinned_policy_is_not_authority(ed, monkeypatch):
    monkeypatch.setattr(s, "PINNED_POLICY_SHA256", None)
    with pytest.raises(s.SigningRefused, match="signing_policy_untrusted"):
        s.verify_anchor_signature(ed[3])


@pytest.mark.parametrize("change", ["extra", "bool_version", "unknown_algorithm", "relocated_public", "noncanonical", "duplicate"])
def test_even_pinned_policy_must_have_exact_canonical_schema(ed, monkeypatch, change):
    root, _, public, _ = ed
    policy = s.policy_document(public)
    if change == "extra": policy["ignored"] = True
    elif change == "bool_version": policy["schema_version"] = True
    elif change == "unknown_algorithm": policy["algorithm"] = s.ALG
    elif change == "relocated_public": policy["public_key_path"] = "../outside"
    raw = s.canonical_policy_bytes(policy)
    if change == "noncanonical": raw = raw.rstrip(b"\n")
    if change == "duplicate": raw = raw.replace(b'{', b'{"schema_version":1,', 1)
    (root / s.POLICY_PATH).write_bytes(raw)
    monkeypatch.setattr(s, "PINNED_POLICY_SHA256", hashlib.sha256(raw).hexdigest())
    with pytest.raises(s.SigningRefused, match="signing_policy_invalid"):
        s.load_trusted_public_key()


def test_renewal_is_ed_and_valid_before_and_after(ed, monkeypatch):
    _, seed, public, ledger = ed
    before = contents(ledger)
    monkeypatch.setattr(s, "load_ed25519_private_key", lambda: seed)
    signer = s.prepare_signer(ledger)
    assert signer.algorithm == s.ED25519 and signer.public_key == public
    assert signer.public_key_sha256 == hashlib.sha256(public).hexdigest()
    sealed_append(ledger, {"kind": "second"}, "fixture")
    assert ledger.count() == 2 and contents(ledger) != before
    assert require_seal(ledger, "fixture") == s.VERIFIED
    assert s.parse_signature(s.sig_path(ledger).read_bytes())[0] == s.ED25519


@pytest.mark.parametrize("key", [b"bad", b"x"*32])
def test_wrong_private_key_refuses_before_any_append(ed, monkeypatch, key):
    ledger = ed[3]
    before = contents(ledger)
    monkeypatch.setattr(s, "load_ed25519_private_key", lambda: key)
    with pytest.raises(s.SigningRefused, match="signing_key_(invalid|mismatch)"):
        sealed_append(ledger, {"kind": "must not append"}, "fixture")
    assert contents(ledger) == before


def test_missing_backend_never_falls_back_or_loads_secret(ed, monkeypatch):
    """غيابُ المكتبة يمنع **التوقيع والكتابة** قطعًا، ولا يسمح بأي
    رجوعٍ إلى HMAC ولا بتحميل سرّ. أما **القراءة** فتعبر بحالة
    `unverifiable` مُعلَنة والختمُ مفروضٌ كاملًا (ق٢٥: «تعذَّر» ليس
    «فشلًا») — وإقفالُها كان يجعل المستودع غيرَ مقروء لأي مفسِّر بلا
    `cryptography` (تدقيق ق٣٩)."""
    ledger = ed[3]
    before = contents(ledger)
    def unavailable(): raise s.SigningRefused("signing_backend_missing", "synthetic missing backend")
    monkeypatch.setattr(s, "_ed25519_backend", unavailable)
    monkeypatch.setattr(s, "load_key", lambda *a, **k: pytest.fail("حمّل سرًّا!"))
    for operation in (lambda: s.prepare_signer(ledger),
                      lambda: sealed_append(ledger, {"kind": "no"}, "fixture")):
        with pytest.raises(s.SigningRefused, match="signing_backend_missing"):
            operation()
    assert require_seal(ledger, "fixture") == s.UNVERIFIABLE
    monkeypatch.setenv("DIWAN_REQUIRE_SIGNATURE", "1")
    with pytest.raises(s.SigningRefused, match="signing_backend_missing"):
        require_seal(ledger, "fixture")
    assert contents(ledger) == before


def test_signature_replace_failure_keeps_complete_old_bytes(ed, monkeypatch):
    ledger = ed[3]
    old = s.sig_path(ledger).read_bytes()
    def fail(*args): raise OSError("synthetic failure before replace")
    monkeypatch.setattr(s.os, "replace", fail)
    with pytest.raises(OSError):
        s.atomic_write_signature(s.sig_path(ledger), old)
    assert s.sig_path(ledger).read_bytes() == old
    assert list(ledger.path.parent.glob(".*.sig.*")) == []


@pytest.mark.parametrize("artifact", [s.POLICY_PATH, s.PUBLIC_KEY_PATH, "sources/acquisitions.jsonl.anchor.sig"])
def test_policy_and_signature_links_refused(ed, artifact):
    root, _, _, ledger = ed
    path = root / artifact
    alternate = path.with_name("alternate")
    path.rename(alternate)
    path.symlink_to(alternate)
    with pytest.raises(s.SigningRefused):
        s.verify_anchor_signature(ledger)


def test_raw_migration_api_is_explicit_and_portable(ed, tmp_path):
    root, seed, public, ledger = ed
    raw = s.make_signature(ledger, seed, algorithm=s.ED25519, public_key=public, root=root)
    message = s.signature_message(ledger, ledger.anchor_path.read_bytes(), algorithm=s.ED25519, root=root)
    assert s.verify_signature_bytes(raw, message, public_key=public)
    historical = s.make_signature(ledger, b"synthetic old", algorithm=s.ALG, root=root)
    assert s.verify_signature_bytes(historical, s.signature_message(ledger, ledger.anchor_path.read_bytes(), root=root), legacy_key=b"synthetic old")
    moved = Ledger(tmp_path / "moved/sources/acquisitions.jsonl")
    assert s.make_signature(moved, seed, algorithm=s.ED25519, public_key=public,
        root=tmp_path / "moved", anchor_bytes=ledger.anchor_path.read_bytes()) == raw


def test_public_repository_gate_and_dynamic_stream(ed, monkeypatch):
    root, seed, public, ledger = ed
    monkeypatch.setattr(cli, "SIGNED_LEDGERS", frozenset({"sources/acquisitions.jsonl"}))
    assert cli.verify_repository(root, public_key=public) == {"verified": 1, "skipped": 0, "failures": []}
    dynamic = Ledger(root / "streams/new-node/inbox.jsonl")
    dynamic.anchor()
    s.sign_anchor(dynamic, seed)
    result = cli.verify_repository(root, public_key=public)
    assert result == {"verified": 2, "skipped": 1, "failures": []}
    s.sig_path(dynamic).write_bytes(s.make_signature(dynamic, b"old"))
    assert "signature_algorithm_mismatch" in cli.verify_repository(root, public_key=public)["failures"][0]
    assert cli.verify_repository(root, public_key=b"y"*32)["failures"] == ["signing_public_key_mismatch"]


def test_private_loader_refuses_non_macos_before_any_native_or_subprocess_call(monkeypatch):
    monkeypatch.setenv(s.ENV_KEY, "a"*64)
    monkeypatch.setattr(s.sys, "platform", "linux")
    monkeypatch.setattr(s.ctypes, "CDLL", forbidden)
    monkeypatch.setattr(s.subprocess, "run", forbidden)
    with pytest.raises(s.SigningRefused, match="signing_keychain_platform_required"):
        s.load_ed25519_private_key()
    assert s.ED25519_KEYCHAIN_SERVICE != s.KEYCHAIN_SERVICE


def test_explicit_public_only_cli_never_loads_hmac_or_private(ed, monkeypatch):
    root, _, _, ledger = ed
    monkeypatch.setattr(cli, "SIGNED_LEDGERS", frozenset({"sources/acquisitions.jsonl"}))
    monkeypatch.setattr(sys, "argv", ["sign_anchors.py", "verify", "--public-only"])
    assert cli.main() == 0
    s.sig_path(ledger).write_bytes(s.make_signature(ledger, b"historical"))
    assert cli.main() == 1
    monkeypatch.setattr(s, "PINNED_POLICY_SHA256", None)
    (root / s.POLICY_PATH).unlink()
    assert cli.main() == 1


@pytest.mark.parametrize("anchor", [{"count": False, "head": "0"*64}, {"count": 0, "head": "f"*64}])
def test_ed_signed_empty_anchor_requires_exact_count_and_genesis(ed, anchor):
    import json
    _, seed, _, ledger = ed
    ledger.path.write_bytes(b"")
    ledger.anchor_path.write_bytes(json.dumps(anchor).encode())
    s.sign_anchor(ledger, seed)
    with pytest.raises(LedgerCorrupt):
        require_seal(ledger, "fixture")


def test_alias_into_governed_root_cannot_downgrade_policy(ed, tmp_path):
    root, _, _, ledger = ed
    outside = root.parent / (root.name + "-alias")
    outside.symlink_to(root, target_is_directory=True)
    aliased = Ledger(outside / "sources/acquisitions.jsonl", create=False)
    s.sig_path(ledger).write_bytes(s.make_signature(ledger, b"historical"))
    with pytest.raises(s.SigningRefused, match="signature_algorithm_mismatch"):
        s.verify_anchor_signature(aliased, b"historical")


def test_explicit_scope_cannot_escape_root(ed):
    root, _, _, _ = ed
    external = Ledger(root.parent / "outside/synthetic.jsonl", create=False)
    with pytest.raises(s.SigningRefused, match="signing_path_invalid"):
        s.signature_message(external, b"{}", algorithm=s.ED25519, root=root)


@pytest.mark.parametrize("when", ["before", "after"])
def test_hard_interruption_publishes_complete_old_or_new_signature(ed, when):
    _, seed, public, ledger = ed
    destination = s.sig_path(ledger)
    old = destination.read_bytes()
    new = s.make_signature(ledger, seed, algorithm=s.ED25519, public_key=public,
                           anchor_bytes=ledger.anchor_path.read_bytes() + b" ")
    pid = os.fork()
    if pid == 0:
        original = os.replace
        def interrupted(source, target):
            if when == "before": os._exit(71)
            original(source, target)
            os._exit(72)
        s.os.replace = interrupted
        s.atomic_write_signature(destination, new)
        os._exit(99)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == (71 if when == "before" else 72)
    assert destination.read_bytes() == (old if when == "before" else new)
    assert s.parse_signature(destination.read_bytes())[0] == s.ED25519
    s.atomic_write_signature(destination, new)
    assert destination.read_bytes() == new



@pytest.mark.parametrize("signed", [True, False])
def test_internal_alias_cannot_escape_ed_policy_even_without_signature(ed, monkeypatch, signed):
    root, _, _, _ = ed
    external = root.parent / (root.name + "-external")
    ledger = Ledger(external / "acquisitions.jsonl")
    ledger.append({"kind": "synthetic"})
    ledger.anchor()
    if signed:
        s.sig_path(ledger).write_bytes(s.make_signature(ledger, b"historical"))
    import shutil
    shutil.rmtree(root / "sources")
    (root / "sources").symlink_to(external, target_is_directory=True)
    aliased = Ledger(root / "sources/acquisitions.jsonl", create=False)
    before = ledger.path.read_bytes()
    monkeypatch.setattr(s, "load_key", lambda: b"historical")
    for operation in (lambda: require_seal(aliased, "alias"),
                      lambda: sealed_append(aliased, {"kind": "must not append"}, "alias")):
        with pytest.raises(s.SigningRefused, match="signing_path_invalid"):
            operation()
    assert ledger.path.read_bytes() == before
