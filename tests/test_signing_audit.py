"""Audit regressions: every node stream, migration, and public probe boundaries."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.canonical import canonical_bytes
from core.ledger import Ledger, LedgerCorrupt
from core.seal import require_seal, sealed_append
from core import signing
from tools import mac_probe, migrate_signatures as migration, sign_anchors


@pytest.fixture
def environment(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(signing, "ROOT", root)
    monkeypatch.setattr(signing, "PINNED_POLICY_SHA256", None)  # legacy fixture only
    monkeypatch.setattr(migration, "ROOT", root)
    monkeypatch.setattr(sign_anchors, "ROOT", root)
    monkeypatch.setattr(signing, "_keychain_key", lambda: None)
    monkeypatch.setenv("DIWAN_ANCHOR_KEY", "audit-synthetic-key")
    monkeypatch.setenv("DIWAN_REQUIRE_SIGNATURE", "1")
    return root


def seed(root, rel="publish/_manifest.jsonl"):
    ledger = Ledger(root / rel)
    ledger.append({"kind": "audit_fixture"})
    ledger.anchor()
    return ledger


def contents(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file() and not p.name.endswith(".lock")}


def planned(root):
    seed(root)
    raw = canonical_bytes(migration.plan())
    return raw, hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("rel", sorted(signing.INITIAL_SIGNATURE_MIGRATION) + [
    "streams/new-node/inbox.jsonl", "streams/عقدة-جديدة/outbox.jsonl"])
def test_strict_manifest_and_all_node_streams_require_signature(environment, rel):
    ledger = seed(environment, rel)
    before = contents(environment)
    with pytest.raises(signing.SigningRefused) as exc:
        require_seal(ledger, "audit")
    assert exc.value.code == "signature_missing"
    assert contents(environment) == before
    signing.sign_anchor(ledger)
    assert require_seal(ledger, "audit") == signing.VERIFIED
    sealed_append(ledger, {"kind": "next"}, "audit")
    assert ledger.count() == 2 and signing.verify_anchor_signature(ledger)
    signing.sig_path(ledger).unlink()
    with pytest.raises(signing.SigningRefused) as exc:
        require_seal(ledger, "audit")
    assert exc.value.code == "signature_missing"


def test_dynamic_stream_inventory_includes_orphan_signature(environment, monkeypatch, capsys):
    path = environment / "streams" / "future" / "inbox.jsonl.anchor.sig"
    path.parent.mkdir(parents=True)
    path.write_text("orphan")
    monkeypatch.setattr(sign_anchors, "GOVERNING", ())
    monkeypatch.setattr("sys.argv", ["sign_anchors.py", "verify"])
    before = contents(environment)
    assert sign_anchors.main() == 1
    assert "ledger_missing" in capsys.readouterr().out
    assert contents(environment) == before


def test_external_synthetic_stream_not_reclassified(environment):
    outside = environment.parent / (environment.name + "-external")
    ledger = seed(outside, "streams/new-node/inbox.jsonl")
    assert not signing.is_governing(ledger)


def test_plan_is_read_only_and_does_not_load_key(environment, monkeypatch):
    ledger = seed(environment)
    extra = seed(environment, "streams/future/inbox.jsonl")
    monkeypatch.setattr(migration, "load_key", lambda: pytest.fail("plan loaded key"))
    before = contents(environment)
    obj = migration.plan()
    assert [e["path"] for e in obj["entries"]] == ["publish/_manifest.jsonl", "streams/future/inbox.jsonl"]
    assert contents(environment) == before
    assert not signing.sig_path(ledger).exists() and not signing.sig_path(extra).exists()


def test_reviewed_plan_adds_only_signatures_and_can_resume(environment):
    raw, digest = planned(environment)
    before = contents(environment)
    report = migration.apply(raw, digest)
    assert report["signed"] == 1 and report["already_verified"] == 0
    after = contents(environment)
    for name, content in before.items():
        assert after[name] == content
    ledger = Ledger(environment / "publish/_manifest.jsonl", create=False)
    assert signing.verify_anchor_signature(ledger)
    signature_before = after["publish/_manifest.jsonl.anchor.sig"]
    report = migration.apply(raw, digest)
    assert report["signed"] == 0 and report["already_verified"] == 1
    assert contents(environment)["publish/_manifest.jsonl.anchor.sig"] == signature_before


@pytest.mark.parametrize("change", ["append", "corrupt", "anchor", "complete_rewrite"])
def test_migration_refuses_changed_or_corrupt_state_without_signing(environment, change):
    raw, digest = planned(environment)
    ledger = Ledger(environment / "publish/_manifest.jsonl", create=False)
    if change == "append":
        ledger.append({"kind": "tail"})
    elif change == "corrupt":
        ledger.path.write_bytes(ledger.path.read_bytes().replace(b"audit_fixture", b"tamper"))
    elif change == "anchor":
        ledger.anchor_path.write_text('{"head":"bad","count":1}')
    else:
        ledger.path.write_text("")
        ledger.append({"kind": "different_valid_chain"})
        ledger.anchor()
    before = contents(environment)
    with pytest.raises((signing.SigningRefused, LedgerCorrupt)):
        migration.apply(raw, digest)
    assert contents(environment) == before
    assert not signing.sig_path(ledger).exists()


@pytest.mark.parametrize("change", ["hash", "root", "duplicate", "traversal", "shape", "bool_count"])
def test_migration_rejects_unreviewed_or_invalid_plans(environment, change):
    raw, digest = planned(environment)
    obj = json.loads(raw)
    if change == "hash":
        digest = "0" * 64
    else:
        if change == "root":
            obj["root_sha256"] = "0" * 64
        elif change == "duplicate":
            obj["entries"] *= 2
        elif change == "traversal":
            obj["entries"][0]["path"] = "streams/../inbox.jsonl"
        elif change == "shape":
            obj["extra"] = True
        else:
            obj["entries"][0]["count"] = True
        raw = canonical_bytes(obj)
        digest = hashlib.sha256(raw).hexdigest()
    before = contents(environment)
    with pytest.raises(signing.SigningRefused):
        migration.apply(raw, digest)
    assert contents(environment) == before


def test_existing_invalid_signature_not_overwritten(environment):
    raw, digest = planned(environment)
    ledger = Ledger(environment / "publish/_manifest.jsonl", create=False)
    signing.sig_path(ledger).write_text(signing.ALG + ":" + "0" * 64 + "\n")
    before = contents(environment)
    with pytest.raises(signing.SigningRefused) as exc:
        migration.apply(raw, digest)
    assert exc.value.code == "signature_mismatch"
    assert contents(environment) == before


def test_whole_plan_validated_before_first_signature(environment):
    seed(environment)
    bad = seed(environment, "streams/future/inbox.jsonl")
    raw = canonical_bytes(migration.plan())
    digest = hashlib.sha256(raw).hexdigest()
    bad.append({"kind": "unsealed"})
    with pytest.raises((signing.SigningRefused, LedgerCorrupt)):
        migration.apply(raw, digest)
    assert not list(environment.rglob("*.sig"))


@pytest.mark.parametrize("where", ["ledger", "anchor", "signature", "parent", "lock"])
def test_migration_rejects_symlink_paths(environment, where):
    raw, digest = planned(environment)
    ledger = Ledger(environment / "publish/_manifest.jsonl", create=False)
    target = environment / "outside"
    if where == "parent":
        ledger.path.parent.rename(target)
        ledger.path.parent.symlink_to(target, target_is_directory=True)
    else:
        link = {"ledger": ledger.path, "anchor": ledger.anchor_path,
                "signature": signing.sig_path(ledger),
                "lock": ledger.path.with_suffix(".jsonl.lock")}[where]
        if link.exists():
            link.rename(target)
        else:
            target.write_text("not-yours")
        link.symlink_to(target)
    before = target.read_bytes() if target.is_file() else None
    with pytest.raises(signing.SigningRefused) as exc:
        migration.apply(raw, digest)
    assert exc.value.code == "migration_path_invalid"
    if before is not None:
        assert target.read_bytes() == before


def test_general_sign_requires_plan_for_new_scopes(environment, monkeypatch, capsys):
    ledger = seed(environment)
    monkeypatch.setattr(sign_anchors, "GOVERNING", ("publish/_manifest.jsonl",))
    monkeypatch.setattr("sys.argv", ["sign_anchors.py", "--resign"])
    before = contents(environment)
    assert sign_anchors.main() == 1
    assert "initial_signature_plan_required" in capsys.readouterr().out
    assert contents(environment) == before and not signing.sig_path(ledger).exists()


@pytest.mark.parametrize("strict", [True, False])
@pytest.mark.parametrize("rel", ["publish/_manifest.jsonl", "streams/future/inbox.jsonl"])
def test_append_cannot_implicitly_approve_first_signature(environment, monkeypatch, strict, rel):
    ledger = seed(environment, rel)
    if not strict:
        monkeypatch.delenv("DIWAN_REQUIRE_SIGNATURE")
    before = contents(environment)
    with pytest.raises(signing.SigningRefused) as exc:
        sealed_append(ledger, {"kind": "must-not-append"}, "audit")
    assert exc.value.code == "initial_signature_plan_required"
    assert contents(environment) == before and not signing.sig_path(ledger).exists()


def test_public_probe_does_not_run_private_inventory_or_emit_raw_output(monkeypatch):
    calls = []
    marker = "PRIVATE-CREDENTIAL MODEL-ID /Volumes/owner/private-path"
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=marker, stderr=marker)
    monkeypatch.setattr(mac_probe.subprocess, "run", run)
    monkeypatch.setattr(mac_probe.shutil, "which", lambda name: "/private/" + marker)
    monkeypatch.setattr(mac_probe.platform, "machine", lambda: marker)
    report = mac_probe.collect()
    assert marker not in json.dumps(report)
    assert report["host"]["machine"] is None
    assert report["tooling"]["ollama_present"] is True
    assert not any("ollama" in arg or "hermes" in arg for call in calls for arg in call)
    assert "out" not in json.dumps(report) and "err" not in json.dumps(report)


def test_public_probe_keeps_only_closed_numeric_measurements(monkeypatch):
    values = {("/usr/sbin/sysctl", "-n", "hw.memsize"): str(24 * 2**30),
              ("/usr/sbin/sysctl", "-n", "hw.ncpu"): "8",
              ("/usr/bin/sw_vers", "-productVersion"): "27.0",
              ("/usr/bin/git", "--version"): "git version 2.51.0"}
    monkeypatch.setattr(mac_probe, "_value", lambda cmd: values[tuple(cmd)])
    report = mac_probe.collect()
    assert report["host"]["ram_gb"] == 24
    assert report["host"]["cpu_cores"] == 8
    assert report["host"]["macos_version"] == "27.0"
    assert report["tooling"]["git_version"] == "2.51.0"
    assert report["capacity_estimate"]["usable_gb"] == 16


def test_reviewed_workflow_has_no_secret_or_owner_runner_dependency():
    root = Path(__file__).resolve().parent.parent
    workflow = (root / ".github/workflows/verify.yml").read_text()
    assert "runs-on: self-hosted" not in workflow
    assert "contents: read" in workflow and "contents: write" not in workflow
    assert workflow.count("persist-credentials: false") == 1
    assert "runs-on: [self-hosted, Linux, ARM64, diwan-isolated]" in workflow
    assert "secrets." not in workflow and "DIWAN_ANCHOR_KEY" not in workflow
    from tools.verification_checks import commands
    checks = commands("reference-python", "reference-node")
    assert checks[-1].name == "public-signatures"
    assert checks[-1].argv[-1] == "--public-only"
    assert not (root / ".github/workflows/probe.yml").exists()


@pytest.mark.parametrize("point", ["before_publish", "after_publish"])
def test_hard_interruption_never_installs_partial_signature_and_plan_resumes(environment, monkeypatch, point):
    import multiprocessing
    import os
    raw, digest = planned(environment)
    publish = migration._publish_exclusive
    def interrupted(source, destination):
        if point == "before_publish":
            os._exit(71)
        publish(source, destination)
        os._exit(72)
    with monkeypatch.context() as context:
        context.setattr(migration, "_publish_exclusive", interrupted)
        child = multiprocessing.get_context("fork").Process(target=migration.apply, args=(raw, digest))
        child.start()
        child.join(5)
        assert not child.is_alive()
        assert child.exitcode == (71 if point == "before_publish" else 72)
    ledger = Ledger(environment / "publish/_manifest.jsonl", create=False)
    if point == "before_publish":
        assert not signing.sig_path(ledger).exists()
        assert list(ledger.path.parent.glob(".diwan-signing-*.tmp"))
    else:
        assert signing.verify_anchor_signature(ledger)
        assert signing.sig_path(ledger).stat().st_nlink == 1
    report = migration.apply(raw, digest)
    assert report["signed"] == (1 if point == "before_publish" else 0)
    assert signing.verify_anchor_signature(ledger)


def test_exclusive_atomic_publication_never_overwrites_existing_file(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"replacement")
    destination.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        migration._publish_exclusive(source, destination)
    assert source.read_bytes() == b"replacement" and destination.read_bytes() == b"existing"


def test_empty_new_stream_bootstrap_is_explicit_and_then_allows_first_emit(environment):
    seed(environment)
    ledger = Ledger(environment / "streams/future/inbox.jsonl")
    before = contents(environment)
    with pytest.raises(signing.SigningRefused, match="migration_path_missing"):
        migration.plan()
    raw = canonical_bytes(migration.plan(initialize_empty_streams=True))
    assert contents(environment) == before and not ledger.anchor_path.exists()
    result = migration.apply(raw, hashlib.sha256(raw).hexdigest())
    assert result["empty_anchors_created"] == 1
    assert signing.verify_anchor_signature(ledger)
    sealed_append(ledger, {"kind": "first-real-entry"}, "new stream")
    assert ledger.count() == 1 and signing.verify_anchor_signature(ledger)


@pytest.mark.parametrize("contents_before", [b"\n", b" ", b'{}\n'])
def test_empty_stream_option_cannot_bless_unanchored_existing_content(environment, contents_before):
    seed(environment)
    ledger = Ledger(environment / "streams/future/inbox.jsonl")
    ledger.path.write_bytes(contents_before)
    before = contents(environment)
    with pytest.raises(signing.SigningRefused, match="migration_empty_stream_invalid"):
        migration.plan(initialize_empty_streams=True)
    assert contents(environment) == before


def test_bootstrap_plan_resumes_after_atomic_empty_anchor_was_installed(environment, monkeypatch):
    seed(environment)
    ledger = Ledger(environment / "streams/future/inbox.jsonl")
    raw = canonical_bytes(migration.plan(initialize_empty_streams=True))
    digest = hashlib.sha256(raw).hexdigest()
    install = migration._install_bytes
    class Interruption(BaseException):
        pass
    def after_anchor(target, blob):
        install(target, blob)
        if target == ledger.anchor_path:
            raise Interruption
    with monkeypatch.context() as context:
        context.setattr(migration, "_install_bytes", after_anchor)
        with pytest.raises(Interruption):
            migration.apply(raw, digest)
    assert ledger.anchor_path.exists() and not signing.sig_path(ledger).exists()
    result = migration.apply(raw, digest)
    assert result["signed"] == 1 and result["already_verified"] == 1
    assert signing.verify_anchor_signature(ledger)
