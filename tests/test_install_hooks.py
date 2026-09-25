"""Installer proofs use disposable local repositories and private synthetic state."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools import install_hooks as installer
from tools.gate_helpers import GateError, digest


PY_EXEC = Path(shutil.which("python3.14") or sys.executable)


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
                            "GIT_CONFIG_NOSYSTEM": "1"})
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def setup(tmp_path):
    tmp_path = tmp_path.resolve()
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "synthetic@example.invalid")
    git(repo, "config", "user.name", "Synthetic test")
    # The installer must honor real global hooksPath in production; these
    # synthetic tests explicitly override it to prevent touching any host hook.
    git(repo, "config", "core.hooksPath", str(repo / ".git/hooks"))
    (repo / "sentinel").write_text("owner")
    git(repo, "add", "sentinel")
    git(repo, "commit", "-qm", "synthetic")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    trust = state / "trust.json"
    trust.write_text(json.dumps({"schema_version": 1, "algorithm": "ED25519",
                                "public_key_hex": "1" * 64, "policy_sha256": "2" * 64}))
    trust.chmod(0o600)
    runtime = state / "runtime.json"
    runtime.write_text(json.dumps({"schema_version": 1, "image_id": "sha256:" + "3" * 64,
                                  "lock_sha256": "4" * 64,
                                  "trust_sha256": digest(trust.read_bytes()),
                                  "python_version": "3.14.7", "node_major": 24}))
    runtime.chmod(0o600)
    # A synthetic trusted source satisfies installation without depending on
    # other agents' unmerged signing changes. No candidate code is copied.
    source = tmp_path / "reviewed"
    (source / "tools").mkdir(parents=True)
    (source / "core").mkdir()
    (source / "core/signing.py").write_text("PINNED_POLICY_SHA256 = '" + "2" * 64 + "'\n")
    for name in ("verify_gate.py", "gate_helpers.py"):
        shutil.copyfile(installer.ROOT / "tools" / name, source / "tools" / name)
    (source / "tools/sign_anchors.py").write_text("def verify_repository(*a,**k): raise RuntimeError('not invoked')\n")
    (source / "tools/verification_checks.py").write_text("def commands(*a): raise RuntimeError('not invoked')\n")
    shutil.copyfile(installer.ROOT / "tools/agent_attribution.py", source / "tools/agent_attribution.py")
    return repo, state, runtime, trust, source


def install(setup):
    repo, state, runtime, trust, source = setup
    py_exec = shutil.which("python3.14") or sys.executable
    return installer.install(repo, state, runtime, trust, source=source, python=Path(py_exec))


def test_installs_private_frozen_bundle_and_uninstalls_only_owned(setup):
    repo, state, _, _, source = setup
    assert install(setup)["code"] == "installed"
    manifest = json.loads((state / "installation.json").read_text())
    bundle = Path(manifest["bundle"])
    hook = installer.hook_location(repo)
    assert bundle.is_relative_to(state) and str(source) not in hook.read_text()
    assert "--repo ." in hook.read_text()
    assert bundle.stat().st_mode & 0o777 == 0o700
    assert (bundle / "trust.json").stat().st_mode & 0o777 == 0o600
    assert (bundle / "tools/verify_gate.py").read_bytes() == (source / "tools/verify_gate.py").read_bytes()
    assert install(setup)["code"] == "already_installed"
    assert installer.uninstall(repo, state)["code"] == "owned_hook_removed"
    assert not hook.exists() and (repo / "sentinel").read_text() == "owner"


def test_previous_hook_preserved_chained_same_stdin_and_args(setup):
    repo, state, _, _, _ = setup
    hook = installer.hook_location(repo)
    log = state / "previous-input"
    hook.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {str(log)!r}\ncat >> {str(log)!r}\nexit 17\n')
    hook.chmod(0o755)
    original, original_mode = hook.read_bytes(), hook.stat().st_mode
    install(setup)
    oid = git(repo, "rev-parse", "HEAD")
    raw = f"HEAD {oid} refs/heads/main {'0' * 40}\n".encode()
    # Skip only our gate: the previous hook must still see and veto the push.
    result = subprocess.run([str(hook), "origin", "local-synthetic"], input=raw,
                            capture_output=True, cwd=repo,
                            env={**os.environ, "DIWAN_SKIP_GATE": "1"})
    assert result.returncode == 17, result.stderr
    report = json.loads(result.stdout)
    assert report["bypass"] is True and report["refs"][0]["local_oid"] == oid
    assert log.read_bytes() == b"origin\nlocal-synthetic\n" + raw
    assert installer.uninstall(repo, state)["code"] == "previous_restored"
    assert hook.read_bytes() == original and hook.stat().st_mode == original_mode


def test_previous_nonexecutable_hook_restored_but_not_run(setup):
    repo, state, *_ = setup
    hook = installer.hook_location(repo)
    hook.write_text("unowned non-executable data")
    hook.chmod(0o600)
    install(setup)
    assert "--previous-hook" not in hook.read_text()
    installer.uninstall(repo, state)
    assert hook.read_text() == "unowned non-executable data"
    assert hook.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("relative", [False, True])
def test_respects_configured_hookspath(setup, relative):
    repo, state, *_ = setup
    directory = repo / "custom" if relative else state / "custom"
    git(repo, "config", "core.hooksPath", "custom" if relative else str(directory))
    install(setup)
    assert (directory / "pre-push").is_file()
    assert not (repo / ".git/hooks/pre-push").exists()
    installer.uninstall(repo, state)


def test_linked_worktree_uses_common_hook_without_binding_wrong_repo(setup):
    repo, state, runtime, trust, source = setup
    other = state.parent / "linked"
    git(repo, "worktree", "add", "-qb", "linked", str(other))
    assert installer.hook_location(repo) == installer.hook_location(other)
    installer.install(other, state, runtime, trust, source=source, python=PY_EXEC)
    hook = installer.hook_location(repo)
    assert "--repo ." in hook.read_text() and str(other) not in hook.read_text()
    assert installer.uninstall(repo, state)["status"] == "uninstalled"


def test_state_inside_any_linked_worktree_is_rejected(setup):
    repo, state, runtime, trust, source = setup
    other = state.parent / "linked"
    git(repo, "worktree", "add", "-qb", "linked", str(other))
    with pytest.raises(GateError, match="trusted_path_inside_candidate"):
        installer.install(repo, other / "private", runtime, trust, source=source)


def test_modified_owned_hook_not_overwritten_or_removed(setup):
    repo, state, *_ = setup
    install(setup)
    hook = installer.hook_location(repo)
    hook.write_text(hook.read_text() + "# owner later edit\n")
    with pytest.raises(GateError, match="hook_modified_refused"):
        installer.uninstall(repo, state)
    with pytest.raises(GateError, match="installation_state_conflict"):
        install(setup)
    assert "owner later edit" in hook.read_text()


def test_modified_previous_hook_not_destroyed(setup):
    repo, state, *_ = setup
    hook = installer.hook_location(repo)
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o700)
    install(setup)
    manifest = json.loads((state / "installation.json").read_text())
    previous = Path(manifest["previous"])
    previous.write_text("changed by owner")
    with pytest.raises(GateError, match="previous_hook_modified_refused"):
        installer.uninstall(repo, state)
    assert previous.read_text() == "changed by owner" and hook.exists()


def test_symlink_hook_refused_without_touching_target(setup):
    repo, state, *_ = setup
    target = state / "owner-hook"
    target.write_text("owner sentinel")
    installer.hook_location(repo).symlink_to(target)
    with pytest.raises(GateError, match="hook_path_symlink"):
        install(setup)
    assert target.read_text() == "owner sentinel"


def test_disabled_hooks_are_not_reenabled(setup):
    repo, *_ = setup
    git(repo, "config", "core.hooksPath", os.devnull)
    with pytest.raises(GateError, match="hooks_disabled"):
        install(setup)
    assert git(repo, "config", "--get", "core.hooksPath") == os.devnull


def test_python3_alias_version_is_not_assumed(setup):
    repo, state, runtime, trust, source = setup
    fake = state / "python3"
    fake.write_text("#!/bin/sh\nprintf '3.9.6\\n'\n")
    fake.chmod(0o700)
    with pytest.raises(GateError, match="python_runtime_invalid"):
        installer.install(repo, state, runtime, trust, source=source, python=fake)
    assert not installer.hook_location(repo).exists()


# The supported range is a contract: pyproject declares requires-python >= 3.11
# while 3.14.7 is the reference runtime (ق٢). Both bounds are exercised with
# synthetic interpreters, so the guard is proven without installing real ones —
# a CI matrix that reuses one interpreter would prove nothing about any bound.
@pytest.mark.parametrize("reported,accepted", [
    ("3.10.14", False),   # below the declared floor
    ("3.11.15", True),    # the floor itself — the cloud session's runtime
    ("3.12.8", True),
    ("3.13.2", True),
    ("3.14.7", True),     # the reference runtime
    ("3.15.0", False),    # above the practical ceiling
    ("4.0.0", False),
    ("", False),          # a silent interpreter is not a passing one
    ("not.a.version", False),
])
def test_supported_python_range_is_enforced_at_both_bounds(setup, reported, accepted):
    repo, state, runtime, trust, source = setup
    fake = state / "synthetic-python"
    fake.write_text("#!/bin/sh\nprintf '%s\\n'\n" % reported)
    fake.chmod(0o700)
    if accepted:
        assert installer.install(repo, state, runtime, trust,
                                 source=source, python=fake)["code"] == "installed"
        assert installer.hook_location(repo).is_file()
    else:
        with pytest.raises(GateError, match="python_runtime_invalid"):
            installer.install(repo, state, runtime, trust, source=source, python=fake)
        assert not installer.hook_location(repo).exists()


def test_installer_supplies_private_evidence_outside_candidate_and_keeps_bundle_pins(setup):
    repo,state,*_ = setup
    install(setup)
    manifest=json.loads((state/"installation.json").read_text())
    evidence=Path(manifest["evidence_dir"])
    assert evidence==state/"evidence" and evidence.stat().st_mode & 0o777==0o700
    assert not evidence.is_relative_to(repo)
    assert "--evidence-dir" in installer.hook_location(repo).read_text()
    assert str(evidence) in installer.hook_location(repo).read_text()
    bundle=Path(manifest["bundle"])
    assert manifest["bundle_sha256"]["tools/gate_helpers.py"]==digest((bundle/"tools/gate_helpers.py").read_bytes())
    assert install(setup)["code"]=="already_installed"
