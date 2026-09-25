"""Actual filesystem probes plus portable alias/identity simulations; no host key."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import unicodedata

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from core.ledger import Ledger
from core.seal import require_seal, sealed_append
import core.signing as s
from tools import sign_anchors as cli


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "native-caf\u00e9-root"
    root.mkdir()
    seed = Ed25519PrivateKey.generate().private_bytes_raw()
    public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    monkeypatch.setattr(s, "ROOT", root)
    monkeypatch.setattr(cli, "SIGNED_LEDGERS", frozenset({"sources/acquisitions.jsonl"}))
    (root / "keys").mkdir()
    (root / s.PUBLIC_KEY_PATH).write_bytes(public)
    policy = s.canonical_policy_bytes(s.policy_document(public))
    (root / s.POLICY_PATH).write_bytes(policy)
    monkeypatch.setattr(s, "PINNED_POLICY_SHA256", hashlib.sha256(policy).hexdigest())
    def forbidden(*args, **kwargs): pytest.fail("filesystem probe attempted secret lookup")
    monkeypatch.setattr(s, "load_key", forbidden)
    monkeypatch.setattr(s, "_keychain_key", forbidden)
    monkeypatch.setattr(s, "load_ed25519_private_key", forbidden)
    ledger = Ledger(root / "sources/acquisitions.jsonl")
    ledger.append({"kind": "synthetic"})
    ledger.anchor()
    s.sign_anchor(ledger, seed)
    assert require_seal(ledger, "canonical") == s.VERIFIED
    return root, seed, public, ledger


def require_native_alias(alias, original):
    if not alias.exists() or not os.path.samefile(alias, original):
        pytest.skip("this filesystem does not resolve the requested case/Unicode spelling to the same inode")
    assert str(alias) != str(original)


@pytest.mark.parametrize("kind", ["root_case", "directory_case", "filename_case", "root_unicode", "node_unicode"])
@pytest.mark.parametrize("signed", [False, True])
def test_actual_filesystem_aliases_never_downgrade_ed(project, kind, signed):
    root, seed, _, ledger = project
    if kind == "root_case":
        alias = root.with_name(root.name.upper()) / "sources/acquisitions.jsonl"
    elif kind == "directory_case":
        alias = root / "SOURCES/acquisitions.jsonl"
    elif kind == "filename_case":
        alias = root / "sources/ACQUISITIONS.JSONL"
    elif kind == "root_unicode":
        alias = root.with_name(unicodedata.normalize("NFD", root.name)) / "sources/acquisitions.jsonl"
    else:
        ledger = Ledger(root / "streams/caf\u00e9-node/inbox.jsonl")
        ledger.append({"kind": "synthetic"})
        ledger.anchor()
        s.sign_anchor(ledger, seed)
        alias = root / "streams/cafe\u0301-node/inbox.jsonl"
    require_native_alias(alias, ledger.path)
    if not signed: s.sig_path(ledger).unlink()
    before = ledger.path.read_bytes()
    aliased = Ledger(alias, create=False)
    operations = [lambda: s._project_scope(aliased, root), lambda: s.is_governing(aliased),
                  lambda: s.ledger_scope(aliased), lambda: s.expected_algorithm(aliased),
                  lambda: require_seal(aliased, "alias"),
                  lambda: sealed_append(aliased, {"kind": "must not append"}, "alias"),
                  lambda: s.read_regular(alias, root=root)]
    for operation in operations:
        with pytest.raises(s.SigningRefused, match="signing_path_invalid"):
            operation()
    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize("kind", ["case", "unicode"])
def test_public_verifier_refuses_noncanonical_root(project, kind):
    root, _, public, _ = project
    alias = root.with_name(root.name.upper() if kind == "case" else unicodedata.normalize("NFD", root.name))
    require_native_alias(alias, root)
    assert cli.verify_repository(alias, public_key=public)["failures"] == ["signing_path_invalid"]


def test_public_verifier_refuses_required_component_renamed_to_alias(project):
    root, _, public, _ = project
    parent = root / "sources"
    renamed = root / "SOURCES"
    parent.rename(renamed)
    require_native_alias(parent, renamed)
    assert cli.verify_repository(root, public_key=public)["failures"]


def test_external_symlink_to_wrong_case_root_cannot_escape_policy(project, tmp_path):
    root, _, _, ledger = project
    wrong = root.with_name(root.name.upper())
    require_native_alias(wrong, root)
    link = tmp_path / "outside-link"
    link.symlink_to(wrong, target_is_directory=True)
    s.sig_path(ledger).unlink()
    with pytest.raises(s.SigningRefused, match="signing_path_invalid"):
        require_seal(Ledger(link / "sources/acquisitions.jsonl", create=False), "alias")


@pytest.mark.parametrize("actual_name", ["PAYLOAD", "cafe\u0301"])
def test_portable_directory_entry_spelling_alias_is_rejected(tmp_path, monkeypatch, actual_name):
    requested = "payload" if actual_name == "PAYLOAD" else "caf\u00e9"
    path = tmp_path / requested
    path.write_bytes(b"synthetic")
    original = os.scandir
    inode = path.lstat()
    @contextmanager
    def scan(parent):
        if Path(parent) == tmp_path:
            yield iter([SimpleNamespace(name=actual_name, stat=lambda **kwargs: inode)])
        else:
            with original(parent) as entries:
                yield entries
    monkeypatch.setattr(s.os, "scandir", scan)
    with pytest.raises(s.SigningRefused, match="signing_path_invalid"):
        s.read_regular(path, root=tmp_path)


def test_portable_same_spelling_different_identity_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "payload"
    path.write_bytes(b"synthetic")
    original = os.scandir
    expected = path.lstat()
    @contextmanager
    def scan(parent):
        if Path(parent) == tmp_path:
            yield iter([SimpleNamespace(name=path.name, stat=lambda **kwargs:
                SimpleNamespace(st_dev=expected.st_dev, st_ino=expected.st_ino + 1))])
        else:
            with original(parent) as entries:
                yield entries
    monkeypatch.setattr(s.os, "scandir", scan)
    with pytest.raises(s.SigningRefused, match="signing_path_invalid"):
        s.read_regular(path, root=tmp_path)


def test_existing_exact_spelling_and_nonexistent_suffix_remain_valid(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    path = root / "sources/acquisitions.jsonl"
    assert s._project_scope(Ledger(path, create=False), root) == "sources/acquisitions.jsonl"
    ledger = Ledger(path)
    assert s.read_regular(ledger.path, root=root) == b""


def test_distinct_case_sensitive_files_are_not_merged(tmp_path):
    lower = tmp_path / "payload"
    lower.write_bytes(b"lower")
    upper = tmp_path / "PAYLOAD"
    if upper.exists() and os.path.samefile(upper, lower):
        pytest.skip("this filesystem cannot hold these two case-distinct directory entries")
    upper.write_bytes(b"upper")
    assert not os.path.samefile(upper, lower)
    assert s.read_regular(lower, root=tmp_path) == b"lower"
    assert s.read_regular(upper, root=tmp_path) == b"upper"


@pytest.mark.parametrize("artifact", ["ledger", "anchor", "both"])
def test_external_hardlink_cannot_become_unsigned_writer(project, tmp_path, artifact):
    _, _, _, original = project
    alias = Ledger(tmp_path / "external-alias.jsonl", create=False)
    if artifact in ("ledger", "both"):
        os.link(original.path, alias.path)
    else:
        alias.path.write_bytes(original.path.read_bytes())
    if artifact in ("anchor", "both"):
        os.link(original.anchor_path, alias.anchor_path)
    else:
        alias.anchor_path.write_bytes(original.anchor_path.read_bytes())
    before = original.path.read_bytes()
    for operation in (lambda: require_seal(alias, "external hardlink"),
                      lambda: sealed_append(alias, {"kind": "must not append"}, "external hardlink")):
        with pytest.raises(s.SigningRefused, match="signing_path_invalid"):
            operation()
    assert original.path.read_bytes() == before


@pytest.mark.parametrize("via_symlink", [False, True])
def test_parent_traversal_is_rejected_before_path_normalization(project, tmp_path, via_symlink):
    root, _, _, original = project
    if via_symlink:
        outside = tmp_path / "external/nested"
        outside.mkdir(parents=True)
        (root / "jump").symlink_to(outside, target_is_directory=True)
        target = Ledger(outside.parent / "victim.jsonl")
        target.path.write_bytes(original.path.read_bytes())
        target.anchor_path.write_bytes(original.anchor_path.read_bytes())
        path = root / "jump/../victim.jsonl"
    else:
        target = original
        path = root / "sources/../sources/acquisitions.jsonl"
    assert path.exists() and os.path.samefile(path, target.path)
    before = target.path.read_bytes()
    ledger = Ledger(path, create=False)
    for operation in (lambda: s._project_scope(ledger, root), lambda: s.is_governing(ledger),
                      lambda: s.ledger_scope(ledger), lambda: s.expected_algorithm(ledger),
                      lambda: s.read_regular(path, root=root),
                      lambda: require_seal(ledger, "parent traversal"),
                      lambda: sealed_append(ledger, {"kind": "must not append"}, "parent traversal")):
        with pytest.raises(s.SigningRefused, match="signing_path_invalid"):
            operation()
    assert target.path.read_bytes() == before


def test_trusted_root_itself_must_not_contain_parent_traversal(project):
    root, _, _, ledger = project
    alias_root = root / ".." / root.name
    assert os.path.samefile(alias_root, root)
    with pytest.raises(s.SigningRefused, match="signing_path_invalid"):
        s.read_regular(ledger.path, root=alias_root)
    with pytest.raises(s.SigningRefused, match="signing_path_invalid"):
        s._project_scope(ledger, alias_root)
