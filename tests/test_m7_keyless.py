"""M7 may inspect project data; only disposable fixtures may be written."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import acceptance_m7 as m7
from core.ledger import Ledger
import core.signing as signing


def _fingerprint(root: Path):
    return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def _forbid_lookup(*args, **kwargs):
    raise AssertionError("production secret lookup attempted")


def _source_root(root: Path):
    with m7.keyless_acceptance():
        m7.synthetic_fixture(root)
        ledger = Ledger(root / "publish" / "_manifest.jsonl")
        ledger.append({"kind": "publish_run", "published": 99, "skipped": 99})
        ledger.anchor()
        m7.sign_anchor(ledger, key=m7.TEST_KEY)


def test_main_keyless_preserves_signed_source_bytes_mtime_and_environment(tmp_path, monkeypatch):
    source = tmp_path / "project"
    # A synthetic source namespace uses legacy signatures regardless of the
    # checked-out production policy, allowing this regression after migration.
    monkeypatch.setattr(signing, "ROOT", tmp_path / "policy-root")
    monkeypatch.setattr(signing, "PINNED_POLICY_SHA256", None, raising=False)
    _source_root(source)
    before = _fingerprint(source)
    monkeypatch.setattr(m7, "ROOT", source)
    monkeypatch.setattr(signing, "load_key", _forbid_lookup)
    monkeypatch.setattr(signing, "_keychain_key", _forbid_lookup)
    if hasattr(signing, "load_ed25519_private_key"):
        monkeypatch.setattr(signing, "load_ed25519_private_key", _forbid_lookup)
    monkeypatch.setenv("DIWAN_ANCHOR_KEY", "synthetic-owner-sentinel-do-not-use")
    monkeypatch.setenv("DIWAN_REQUIRE_SIGNATURE", "1")
    env_before = dict(os.environ)
    assert m7.main() == 0
    assert _fingerprint(source) == before
    assert dict(os.environ) == env_before
    assert signing.load_key is _forbid_lookup
    assert signing._keychain_key is _forbid_lookup
    # Re-running must not accumulate acceptance checks or source state.
    count = len(m7.CHECKS)
    assert m7.main() == 0
    assert len(m7.CHECKS) == count
    assert _fingerprint(source) == before


def test_keyless_context_blocks_legacy_and_ed_loaders_and_restores_on_error(monkeypatch):
    monkeypatch.setattr(signing, "load_key", _forbid_lookup)
    monkeypatch.setattr(signing, "_keychain_key", _forbid_lookup)
    monkeypatch.setattr(signing, "load_ed25519_private_key", _forbid_lookup, raising=False)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        with m7.keyless_acceptance():
            for loader in (signing.load_key, signing._keychain_key, signing.load_ed25519_private_key):
                with pytest.raises(signing.SigningRefused) as caught:
                    loader()
                assert caught.value.code == "signing_key_missing"
            raise RuntimeError("synthetic failure")
    assert signing.load_key is _forbid_lookup
    assert signing.load_ed25519_private_key is _forbid_lookup


def test_read_only_source_check_detects_internal_text_in_existing_projection(tmp_path, monkeypatch):
    monkeypatch.setattr(signing, "ROOT", tmp_path / "policy-root")
    monkeypatch.setattr(signing, "PINNED_POLICY_SHA256", None, raising=False)
    source = tmp_path / "project"
    _source_root(source)
    import json
    (source / "publish" / "leak.jsonl").write_text(json.dumps({"item": {
        "text": "نص داخلي محض لا يجوز أن يظهر في النشر."}}, ensure_ascii=False) + "\n")
    before = _fingerprint(source)
    m7.CHECKS.clear()
    with m7.keyless_acceptance():
        m7.check_sources(source)
    assert not m7.CHECKS[0][1]
    assert _fingerprint(source) == before


def test_shared_checks_use_one_python_and_strict_public_verifier():
    from tools.verification_checks import commands
    checks = commands("/explicit/python", "/explicit/node")
    assert len({check.name for check in checks}) == len(checks)
    assert {c.name for c in checks} == {
        "pytest", "docs", "acceptance-0", "acceptance-7", "acceptance-8b",
        "acceptance-9", "acceptance-10", "acceptance-11", "acceptance-12",
        "acceptance-13", "frontend-syntax", "frontend-behavior", "public-signatures"}
    assert all(c.argv[0] == ("/explicit/node" if c.name.startswith("frontend-")
                            else "/explicit/python") for c in checks)
    assert checks[-1].argv[-1] == "--public-only"


def test_runner_command_exposes_no_host_resource_or_secret():
    from ci.start_isolated_runner import container_command
    command = container_command("sha256:" + "a" * 64, "https://github.com/example/repo",
                                "diwan-isolated-0123456789abcdef")
    assert command[:2] == ["docker", "run"]
    assert command[command.index("--user") + 1] == "1000:1000"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges"
    assert "--read-only" in command and "--rm" in command
    assert not {"--privileged", "--mount", "-v", "--volume", "--env", "-e",
                "--env-file", "--pid", "--network=host", "--ipc"}.intersection(command)
    assert all("docker.sock" not in value and "/Users/" not in value for value in command)
    mounts = [command[i + 1] for i, value in enumerate(command) if value == "--tmpfs"]
    assert len(mounts) == 3
    assert all("uid=1000,gid=1000" in value for value in mounts)


def test_bootstrap_trust_validation_and_exclusive_receipt(tmp_path):
    import json
    from ci.prepare_runtime import validate_trust, write_receipt
    trust = tmp_path / "trust.json"
    trust.write_text(json.dumps({"schema_version": 1, "algorithm": "ED25519",
                                 "public_key_hex": "a" * 64, "policy_sha256": "b" * 64}))
    assert validate_trust(trust) == hashlib.sha256(trust.read_bytes()).hexdigest()
    bad = json.loads(trust.read_text())
    bad["algorithm"] = "HMAC-SHA256"
    trust.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="invalid external public trust"):
        validate_trust(trust)
    receipt = tmp_path / "receipt.json"
    write_receipt(receipt, {"synthetic": 1})
    before = receipt.read_bytes()
    assert receipt.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_receipt(receipt, {"synthetic": 2})
    assert receipt.read_bytes() == before


def test_ci_uses_only_reviewed_isolated_label_and_shared_checks():
    workflow = (Path(__file__).parents[1] / ".github/workflows/verify.yml").read_text()
    assert "[self-hosted, Linux, ARM64, diwan-isolated]" in workflow
    assert "contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "tools/run_verification.py" in workflow
    assert "secrets." not in workflow
    assert "mac-diwan" not in workflow
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow


@pytest.mark.parametrize("list_status,list_output,expected", [(0, b"", True),
    (0, b"synthetic-container\n", False), (1, b"", False)])
def test_runner_disposal_nonzero_requires_confirmed_absence(monkeypatch, list_status, list_output, expected):
    import subprocess
    from ci import start_isolated_runner as bootstrap
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "rm":
            return subprocess.CompletedProcess(argv, 1)
        return subprocess.CompletedProcess(argv, list_status, list_output)
    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    name = "diwan-isolated-0123456789abcdef"
    assert bootstrap.dispose_container(name) is expected
    assert calls[0] == ["docker", "rm", "--force", name]
    assert calls[1][4] == f"name=^/{name}$"


def test_build_image_id_normalizes_buildkit_index_digest(tmp_path, monkeypatch):
    from ci import prepare_runtime as bootstrap
    index = "sha256:" + "a" * 64
    canonical = "sha256:" + "b" * 64
    iid = tmp_path / "iid"
    iid.write_text(index + "\n")
    calls = []
    def run(argv):
        calls.append(argv)
        return canonical
    monkeypatch.setattr(bootstrap, "run", run)
    assert bootstrap.build_image_id(iid) == canonical
    assert calls == [["docker", "image", "inspect", "--format", "{{.Id}}", index]]


@pytest.mark.parametrize("bad", ["node:24", "sha256:bad", "sha256:" + "a" * 64 + "\nsha256:" + "b" * 64])
def test_build_image_id_rejects_non_single_digest(tmp_path, monkeypatch, bad):
    from ci import prepare_runtime as bootstrap
    iid = tmp_path / "iid"
    iid.write_text(bad)
    monkeypatch.setattr(bootstrap, "run", lambda argv: pytest.fail("must reject before Docker"))
    with pytest.raises(ValueError, match="SHA-256 image reference"):
        bootstrap.build_image_id(iid)


@pytest.mark.parametrize("already_present", [False, True])
def test_local_runtime_tag_is_identity_derived_and_verified(monkeypatch, already_present):
    from ci import prepare_runtime as bootstrap
    canonical = "sha256:" + "b" * 64
    expected = "diwan-runtime:sha256-" + "b" * 64
    calls = []
    def run(argv):
        calls.append(argv)
        if argv[2] == "inspect":
            return canonical
        if argv[2] == "ls":
            return canonical if already_present else ""
        return ""
    monkeypatch.setattr(bootstrap, "run", run)
    assert bootstrap.local_runtime_tag(canonical) == expected
    tagged = [command for command in calls if command[2] == "tag"]
    assert tagged == ([] if already_present else [["docker", "image", "tag", canonical, expected]])
    assert calls[-1] == ["docker", "image", "inspect", "--format", "{{.Id}}", expected]


def test_local_runtime_tag_never_overwrites_mismatching_existing_tag(monkeypatch):
    from ci import prepare_runtime as bootstrap
    canonical = "sha256:" + "b" * 64
    other = "sha256:" + "c" * 64
    calls = []
    def run(argv):
        calls.append(argv)
        if argv[2] == "inspect":
            return canonical if argv[-1] == canonical else other
        if argv[2] == "ls":
            return other
        pytest.fail("must not retag another image")
    monkeypatch.setattr(bootstrap, "run", run)
    with pytest.raises(ValueError, match="already refers to another image"):
        bootstrap.local_runtime_tag(canonical)
    assert all(command[2] != "tag" for command in calls)


def test_local_runtime_tag_detects_wrong_target_after_creation(monkeypatch):
    from ci import prepare_runtime as bootstrap
    canonical = "sha256:" + "b" * 64
    calls = []
    def run(argv):
        calls.append(argv)
        if argv[2] == "inspect":
            return canonical if argv[-1] == canonical else "sha256:" + "c" * 64
        return ""
    monkeypatch.setattr(bootstrap, "run", run)
    with pytest.raises(ValueError, match="identity verification failed"):
        bootstrap.local_runtime_tag(canonical)
