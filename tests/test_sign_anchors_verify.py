"""التحقق من التوقيع لا ينشئ سجلات ولا يخفي غياب السجل المطلوب."""
import os
import sys
import json

import pytest

from core.ledger import Ledger
import core.signing as signing
from tools import sign_anchors as tool

OPTIONAL = tuple(sorted(tool.OPTIONAL_LOCAL))
REQUIRED = tuple(sorted(signing.SIGNED_LEDGERS - set(OPTIONAL)))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(tool, "ROOT", root)
    monkeypatch.setattr(signing, "ROOT", root)
    monkeypatch.setattr(signing, "PINNED_POLICY_SHA256", None)  # legacy fixture only
    monkeypatch.setattr(signing, "_keychain_key", lambda: None)
    monkeypatch.setenv("DIWAN_ANCHOR_KEY", "synthetic-test-key")
    monkeypatch.setattr(sys, "argv", ["sign_anchors.py", "verify"])

    def select(rel):
        monkeypatch.setattr(tool, "GOVERNING", (rel,))
        return root / rel
    return root, select


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("rel", OPTIONAL)
def test_missing_optional_not_created(setup, rel, capsys):
    root, select = setup
    path = select(rel)
    before = snapshot(root)
    assert tool.main() == 0
    assert "مخزن محلي غائب" in capsys.readouterr().out
    assert not path.parent.exists()
    assert snapshot(root) == before


@pytest.mark.parametrize("rel", REQUIRED)
def test_missing_required_fails_without_creating_files(setup, rel, capsys):
    root, select = setup
    path = select(rel)
    assert tool.main() == 1
    assert "ledger_missing" in capsys.readouterr().out
    assert not path.parent.exists() and snapshot(root) == {}


@pytest.mark.parametrize("rel", OPTIONAL + ("sources/acquisitions.jsonl",))
@pytest.mark.parametrize("artifact", ["anchor", "signature", "both"])
def test_orphan_artifacts_are_not_optional_absence(setup, rel, artifact, capsys):
    root, select = setup
    ledger = Ledger(select(rel), create=False)
    ledger.path.parent.mkdir(parents=True)
    if artifact in ("anchor", "both"):
        ledger.anchor_path.write_text("{}")
    if artifact in ("signature", "both"):
        signing.sig_path(ledger).write_text("orphan")
    before = snapshot(root)
    assert tool.main() == 1
    assert "ledger_missing" in capsys.readouterr().out
    assert not ledger.path.exists() and snapshot(root) == before


@pytest.mark.parametrize("rel", OPTIONAL + ("sources/acquisitions.jsonl",))
@pytest.mark.parametrize("has_content", [False, True])
def test_existing_unanchored_is_failure_without_repair(setup, rel, has_content, capsys):
    root, select = setup
    ledger = Ledger(select(rel))
    if has_content:
        ledger.append({"kind": "fixture"})
    before = snapshot(root)
    assert tool.main() == 1
    assert "anchor_missing" in capsys.readouterr().out
    assert snapshot(root) == before and not ledger.anchor_path.exists()


def seed(path, *, sign=True):
    ledger = Ledger(path)
    ledger.append({"kind": "fixture"})
    ledger.anchor()
    if sign:
        signing.sign_anchor(ledger)
    return ledger


def test_valid_signed_verify_preserves_contents_and_mtime(setup, capsys):
    root, select = setup
    ledger = seed(select("sources/acquisitions.jsonl"))
    for p in (ledger.path, ledger.anchor_path, signing.sig_path(ledger)):
        os.utime(p, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    before = snapshot(root)
    assert tool.main() == 0
    assert "1 مرساة" in capsys.readouterr().out
    assert snapshot(root) == before


@pytest.mark.parametrize("strict", [False, True])
def test_existing_seal_missing_signature_fails_without_signing(setup, monkeypatch, strict):
    root, select = setup
    ledger = seed(select("sources/acquisitions.jsonl"), sign=False)
    if strict:
        monkeypatch.setenv("DIWAN_REQUIRE_SIGNATURE", "1")
    before = snapshot(root)
    assert tool.main() == 1
    assert not signing.sig_path(ledger).exists() and snapshot(root) == before


@pytest.mark.parametrize("strict", [False, True])
def test_first_explicit_sign_of_valid_seal_still_works(setup, monkeypatch, strict):
    root, select = setup
    ledger = seed(select("sources/acquisitions.jsonl"), sign=False)
    if strict:
        monkeypatch.setenv("DIWAN_REQUIRE_SIGNATURE", "1")
    before_ledger = ledger.path.read_bytes()
    before_anchor = ledger.anchor_path.read_bytes()
    monkeypatch.setattr(sys, "argv", ["sign_anchors.py"])
    assert tool.main() == 0
    assert signing.verify_anchor_signature(ledger)
    assert ledger.path.read_bytes() == before_ledger
    assert ledger.anchor_path.read_bytes() == before_anchor


def test_explicit_sign_does_not_create_missing_anchor(setup, monkeypatch, capsys):
    root, select = setup
    ledger = Ledger(select("sources/acquisitions.jsonl"))
    ledger.append({"kind": "fixture"})
    before = snapshot(root)
    monkeypatch.setattr(sys, "argv", ["sign_anchors.py"])
    assert tool.main() == 1
    assert "anchor_missing" in capsys.readouterr().out
    assert snapshot(root) == before


def test_ledger_create_false_constructor_has_no_filesystem_effect(tmp_path):
    path = tmp_path / "absent" / "ledger.jsonl"
    ledger = Ledger(path, create=False)
    assert ledger.path == path and not path.parent.exists()
    with pytest.raises(FileNotFoundError):
        ledger.entries()


def test_default_ledger_constructor_still_creates(tmp_path):
    path = tmp_path / "new" / "ledger.jsonl"
    assert Ledger(path).entries() == [] and path.exists()


@pytest.mark.parametrize("artifact", ["ledger", "anchor", "signature"])
def test_symlink_artifact_is_not_skipped_or_repaired(setup, artifact, capsys):
    root, select = setup
    ledger = Ledger(select(OPTIONAL[0]), create=False)
    ledger.path.parent.mkdir(parents=True)
    target = {"ledger": ledger.path, "anchor": ledger.anchor_path,
              "signature": signing.sig_path(ledger)}[artifact]
    target.symlink_to(root / "missing-target")
    assert tool.main() == 1
    assert "ledger_path_invalid" in capsys.readouterr().out
    assert target.is_symlink() and not (root / "missing-target").exists()


@pytest.mark.parametrize("resign", [False, True])
@pytest.mark.parametrize("corruption", ["tail", "chain", "anchor"])
def test_signing_never_bypasses_content_verification(setup, monkeypatch, resign, corruption):
    root, select = setup
    ledger = seed(select("sources/acquisitions.jsonl"), sign=False)
    if corruption == "tail":
        ledger.append({"kind": "unexpected-tail"})
    elif corruption == "chain":
        ledger.path.write_bytes(ledger.path.read_bytes().replace(b'"fixture"', b'"corrupt"'))
    else:
        ledger.anchor_path.write_bytes(ledger.anchor_path.read_bytes().replace(b'"count":1', b'"count":2'))
    if resign:
        signing.sig_path(ledger).write_text("HMAC-SHA256:" + "0" * 64 + "\n")
    before = snapshot(root)
    monkeypatch.setenv("DIWAN_REQUIRE_SIGNATURE", "1")
    monkeypatch.setattr(sys, "argv", ["sign_anchors.py"] + (["--resign"] if resign else []))
    assert tool.main() == 1
    assert snapshot(root) == before


@pytest.mark.parametrize("resign", [False, True])
def test_resign_still_requires_explicit_option_for_sound_content(setup, monkeypatch, resign):
    root, select = setup
    ledger = seed(select("sources/acquisitions.jsonl"))
    signing.sig_path(ledger).write_text("HMAC-SHA256:" + "0" * 64 + "\n")
    before = snapshot(root)
    monkeypatch.setenv("DIWAN_REQUIRE_SIGNATURE", "1")
    monkeypatch.setattr(sys, "argv", ["sign_anchors.py"] + (["--resign"] if resign else []))
    assert tool.main() == (0 if resign else 1)
    if resign:
        assert signing.verify_anchor_signature(ledger)
        assert ledger.path.read_bytes() == before[str(ledger.path.relative_to(root))][0]
        assert ledger.anchor_path.read_bytes() == before[str(ledger.anchor_path.relative_to(root))][0]
    else:
        assert snapshot(root) == before


@pytest.mark.parametrize("field,value", [("count", False), ("count", -1), ("count", "0"),
                                         ("head", None), ("head", "x" * 64), ("head", "1" * 64)])
def test_first_sign_empty_anchor_requires_exact_typed_checkpoint(setup, monkeypatch, field, value):
    root, select = setup
    ledger = Ledger(select("sources/acquisitions.jsonl"))
    anchor = ledger.anchor()
    anchor[field] = value
    ledger.anchor_path.write_text(json.dumps(anchor))
    before = snapshot(root)
    monkeypatch.setenv("DIWAN_REQUIRE_SIGNATURE", "1")
    monkeypatch.setattr(sys, "argv", ["sign_anchors.py"])
    assert tool.main() == 1
    assert snapshot(root) == before
