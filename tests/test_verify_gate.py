"""Synthetic offline repositories only; no real Docker, hook, or Keychain calls."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from tools import gate_helpers as helper
from tools import verify_gate as gate


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1",
                            "GIT_CONFIG_GLOBAL": os.devnull}, text=True)
    if check:
        assert r.returncode == 0, r.stderr
    return r


@pytest.fixture
def repo(tmp_path):
    root = (tmp_path / "source").resolve()
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "synthetic@example.invalid")
    git(root, "config", "user.name", "Synthetic test")
    (root / "requirements-ci.lock").write_bytes(b"locked\n")
    (root / "marker").write_text("green")
    (root / "signature").write_text("valid")
    (root / "registry").mkdir()
    (root / "registry/agents.json").write_text(json.dumps(REGISTRY), encoding="utf-8")
    commit(root)
    return root


AGENT = "anthropic/claude-opus-5"
REGISTRY = {"schema_version": 1, "trailer": "Diwan-Agent",
            "enforced_from": "2020-01-01T00:00:00Z",
            "limits": ["self_declared_not_authenticated"],
            "agents": {AGENT: {"surface": "synthetic", "admitted": "2020-01-01"}}}


def commit(root, *, agent: str | None = AGENT):
    git(root, "add", ".")
    message = "synthetic" if agent is None else f"synthetic\n\nDiwan-Agent: {agent}\n"
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD").stdout.strip()


@dataclass(frozen=True)
class Check:
    name: str
    argv: tuple[str, ...] = ()
    timeout_s: int = 1


CHECKS = (Check("probe"), Check("public-signatures"))
RECEIPT = {"schema_version": 1, "image_id": "sha256:" + "1" * 64,
           "lock_sha256": helper.digest(b"locked\n"), "python_version": "3.14.7",
           "node_major": 24}
TRUST = {"schema_version": 1, "algorithm": "ED25519", "public_key_hex": "2" * 64,
         "policy_sha256": "3" * 64}


class Runner:
    calls = []

    def __init__(self, receipt):
        pass

    def run(self, root, check):
        self.calls.append(root)
        failed = (root / "marker").read_text() != "green"
        # Candidate writes are confined to its disposable snapshot.
        (root / "generated-sentinel").write_text("candidate mutation")
        return {"name": check.name, "code": "check_failed" if failed else "passed",
                "exit_code": int(failed)}


def signature(root, trust):
    if (root / "signature").read_text() != "valid":
        raise helper.GateError("candidate_signature_failed")
    return {"name": "public-signatures", "code": "passed", "exit_code": 0, "verified": 7}


def evaluate(repo, oids, **kwargs):
    updates = [{"local_oid": oid, "local_ref": "synthetic", "remote_ref": f"refs/heads/{n}",
                "remote_oid": "0" * 40} for n, oid in enumerate(oids)]
    return gate.evaluate(repo, updates, RECEIPT, TRUST, signature_verifier=signature,
                         runner_factory=Runner, checks_factory=lambda: CHECKS, **kwargs)


def test_checks_actual_sha_not_green_head_and_preserves_source(repo):
    green = git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "marker").write_text("red")
    red = commit(repo)
    git(repo, "checkout", "--detach", green)
    (repo / "untracked-sentinel").write_bytes(b"owner bytes")
    before = {str(p.relative_to(repo)): (p.read_bytes(), p.stat().st_mtime_ns)
              for p in repo.iterdir() if p.is_file()}
    result = evaluate(repo, [red])
    assert result["status"] == "failed"
    assert result["refs"][0]["commit"] == red
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == green
    assert before == {str(p.relative_to(repo)): (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in repo.iterdir() if p.is_file()}
    assert not (repo / "generated-sentinel").exists()
    assert all(not p.exists() for p in Runner.calls)


def test_multiple_refs_deduplicate_but_any_red_blocks(repo):
    green = git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "marker").write_text("red")
    red = commit(repo)
    result = evaluate(repo, [green, red, green])
    assert result["status"] == "failed"
    assert [c["sha"] for c in result["commits"]] == [green, red]
    assert len(result["refs"]) == 3


def test_red_candidate_signature_blocks_before_any_code(repo):
    (repo / "signature").write_text("invalid")
    red = commit(repo)
    Runner.calls = []
    result = evaluate(repo, [red])
    assert result["commits"][0]["code"] == "candidate_signature_failed"
    assert Runner.calls == []


def test_lock_change_requires_new_offline_runtime(repo):
    (repo / "requirements-ci.lock").write_text("changed")
    result = evaluate(repo, [commit(repo)])
    assert result["commits"][0]["code"] == "candidate_lock_mismatch"


def test_annotated_and_lightweight_tags_peel_exact_object(repo):
    expected = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "tag", "-am", "synthetic", "annotated")
    oid = git(repo, "rev-parse", "annotated").stdout.strip()
    assert oid != expected
    assert evaluate(repo, [oid])["commits"][0]["sha"] == expected
    git(repo, "tag", "light")
    assert evaluate(repo, [git(repo, "rev-parse", "light").stdout.strip()])["status"] == "passed"


def test_tag_to_blob_is_named_refusal(repo):
    oid = git(repo, "rev-parse", "HEAD:marker").stdout.strip()
    with pytest.raises(helper.GateError, match="push_object_not_commit"):
        evaluate(repo, [oid])


@pytest.mark.parametrize("raw,code", [
    ("bad", "push_input_invalid"),
    (f"(delete) {'0'*40} refs/heads/x {'1'*40}\n", "push_delete_refused"),
    (f"refs/heads/x {'1'*39} refs/heads/x {'0'*40}", "push_input_invalid"),
    (f"refs/heads/x {'1'*40} x {'0'*40}", "push_input_invalid"),
    (f"refs/heads/x {'1'*40} refs/heads/x {'0'*40}\n" * 2, "push_input_invalid"),
])
def test_malformed_and_delete_defined(raw, code):
    with pytest.raises(helper.GateError, match=code):
        helper.parse_updates(raw, 40)


def test_empty_push_does_not_run_checks(repo):
    assert helper.parse_updates("", 40) == []
    result = evaluate(repo, [])
    assert result["status"] == "passed" and result["code"] == "no_updates"


def test_full_oid_required_not_moving_branch(repo):
    with pytest.raises(helper.GateError, match="commit_oid_required"):
        evaluate(repo, ["HEAD"])


def test_raw_snapshot_ignores_archive_attributes_filters_and_hardlinks(repo, tmp_path):
    (repo / ".gitattributes").write_text("marker export-ignore\nraw export-subst\n")
    (repo / "raw").write_bytes(b"$Format:%H$\x00binary\r\n")
    (repo / "executable").write_text("#!/bin/sh\n")
    (repo / "executable").chmod(0o755)
    oid = commit(repo)
    destination = tmp_path / "snapshot"
    destination.mkdir()
    helper.materialize(repo, oid, destination)
    assert (destination / "marker").read_text() == "green"
    assert (destination / "raw").read_bytes() == b"$Format:%H$\x00binary\r\n"
    assert (destination / "raw").stat().st_ino != (repo / "raw").stat().st_ino
    assert (destination / "executable").stat().st_mode & 0o111


def test_symlink_tree_fails_before_host_verification(repo):
    (repo / "outside-link").symlink_to("/etc/passwd")
    result = evaluate(repo, [commit(repo)])
    assert result["commits"][0]["code"] == "tree_type_unsupported"


def test_moving_branch_cannot_change_frozen_object(repo, monkeypatch):
    frozen = git(repo, "rev-parse", "HEAD").stdout.strip()
    original = helper.materialize

    def move_then_copy(repository, oid, dest):
        (repo / "marker").write_text("red")
        commit(repo)
        original(repository, oid, dest)

    monkeypatch.setattr(gate, "materialize", move_then_copy)
    result = evaluate(repo, [frozen])
    assert result["status"] == "passed"
    assert result["commits"][0]["sha"] == frozen
    assert git(repo, "rev-parse", "HEAD").stdout.strip() != frozen


def configurations(tmp_path, repo):
    state = (tmp_path / "private").resolve()
    state.mkdir(mode=0o700)
    trust = state / "trust.json"
    trust.write_text(json.dumps(TRUST))
    trust.chmod(0o600)
    runtime = state / "runtime.json"
    runtime.write_text(json.dumps({**RECEIPT, "trust_sha256": helper.digest(trust.read_bytes())}))
    runtime.chmod(0o600)
    return runtime, trust


def test_private_configuration_authenticated(tmp_path, repo):
    runtime, trust = configurations(tmp_path, repo)
    assert helper.load_configuration(runtime, trust, repo)[1] == TRUST
    trust.write_text(trust.read_text().replace("ED25519", "HMAC-SHA256"))
    with pytest.raises(helper.GateError, match="runtime_receipt_invalid"):
        helper.load_configuration(runtime, trust, repo)


def test_publicly_writable_configuration_refused(tmp_path, repo):
    runtime, trust = configurations(tmp_path, repo)
    trust.chmod(0o644)
    with pytest.raises(helper.GateError, match="trusted_path_permissions"):
        helper.load_configuration(runtime, trust, repo)


def test_no_candidate_environment_forwarded(monkeypatch):
    monkeypatch.setenv("DIWAN_ANCHOR_KEY", "synthetic-secret")
    monkeypatch.setenv("GIT_DIR", "wrong")
    monkeypatch.setenv("PYTHONPATH", "wrong")
    assert not {"DIWAN_ANCHOR_KEY", "GIT_DIR", "PYTHONPATH"} & helper.clean_env().keys()


def test_docker_is_offline_and_only_candidate_is_mounted(monkeypatch, tmp_path):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps([{
                "Id": RECEIPT["image_id"], "Config": {"Labels": {
                    "diwan.lock-sha256": RECEIPT["lock_sha256"]}}}]).encode())
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(helper.shutil, "which", lambda *a, **k: "/trusted/docker")
    monkeypatch.setattr(helper.subprocess, "run", run)
    runner = helper.DockerRunner(RECEIPT)
    assert runner.run(tmp_path, Check("probe", ("python", "check.py")))["code"] == "passed"
    assert "--mount" not in calls[1][0]  # trusted runtime probe sees no candidate
    assert "importlib.metadata" in calls[1][0][-1]
    command = next(argv for argv, _ in calls if "--mount" in argv)
    assert "--network=none" in command and "--pull=never" in command
    assert "--read-only" in command and "--cap-drop=ALL" in command
    assert "--memory=6g" in command and "--cpus=4" in command
    # Synthetic Git hooks and subprocess fixtures must actually execute on
    # Linux; a noexec /tmp makes Git silently skip hooks and invalidates proof.
    assert command[command.index("--tmpfs") + 1] == "/tmp:rw,exec,nosuid,nodev,size=1g,mode=1777"
    assert command.count("--mount") == 1
    assert f"type=bind,src={tmp_path},dst=/workspace" in command
    assert not any("docker.sock" in x or "/Users/" in x for x in command if x != str(tmp_path)
                   and not x.startswith("type=bind"))
    assert calls[-1][0][1:3] == ["rm", "--force"]


def test_docker_timeout_removes_only_owned_container(monkeypatch, tmp_path):
    calls = []
    runner = object.__new__(helper.DockerRunner)
    runner.receipt, runner.executable = RECEIPT, "/trusted/docker"

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "run":
            raise subprocess.TimeoutExpired(argv, 1)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(helper.subprocess, "run", run)
    result = runner.run(tmp_path, Check("probe"))
    assert result["code"] == "check_timeout"
    assert calls[1][3].startswith("diwan-gate-")
    assert calls[0][calls[0].index("--name") + 1] == calls[1][3]


@pytest.mark.parametrize("ps_code,remaining,want", [(0, "", "passed"),
    (0, "container-id", "runtime_cleanup_failed"), (1, "", "runtime_cleanup_failed")])
def test_cleanup_nonzero_requires_proven_container_absence(monkeypatch, tmp_path, ps_code, remaining, want):
    runner = object.__new__(helper.DockerRunner)
    runner.receipt, runner.executable = RECEIPT, "/trusted/docker"
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "rm":
            return subprocess.CompletedProcess(argv, 1)
        if argv[1] == "ps":
            return subprocess.CompletedProcess(argv, ps_code, remaining)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(helper.subprocess, "run", run)
    assert runner.run(tmp_path, Check("probe"))["code"] == want
    name = calls[0][calls[0].index("--name") + 1]
    assert f"name=^/{name}$" in calls[-1]


def test_explicit_skip_can_record_delete_without_running_gate():
    raw = f"(delete) {'0'*40} refs/heads/deleted {'1'*40}\n"
    assert helper.parse_updates(raw, 40, allow_delete=True)[0]["local_oid"] == "0" * 40


@pytest.fixture
def push_harness(tmp_path, repo):
    """Real Git pre-push; replace only container execution with a small probe.

    This avoids recursively running the project's gate/tests, while exercising
    stdin parsing, actual objects, trusted verification and raw extraction.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw()
    (repo / "governed").write_bytes(b"synthetic approved bytes")
    (repo / "signature").write_text(key.sign((repo / "governed").read_bytes()).hex())
    commit(repo)
    runtime, trust = configurations(tmp_path, repo)
    trust.write_text(json.dumps({**TRUST, "public_key_hex": public.hex()}))
    runtime.write_text(json.dumps({**RECEIPT, "trust_sha256": helper.digest(trust.read_bytes())}))
    source = Path(gate.__file__).resolve().parent.parent
    harness = tmp_path / "harness.py"
    harness.write_text(f'''import sys
sys.path.insert(0, {str(source)!r})
from tools import verify_gate as g
from tools.gate_helpers import GateError
from dataclasses import dataclass
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
@dataclass
class Check:
    name: str
class Runner:
    def __init__(self, receipt): pass
    def run(self, root, check):
        code = int((root / "marker").read_text() != "green")
        (root / "generated").write_text("snapshot only")
        return {{"name":check.name,"code":"check_failed" if code else "passed","exit_code":code}}
def signature(root, trust):
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(trust["public_key_hex"])).verify(bytes.fromhex((root / "signature").read_text()), (root / "governed").read_bytes())
    except Exception:
        raise GateError("candidate_signature_failed")
    return {{"name":"public-signatures","code":"passed","exit_code":0,"verified":7}}
original = g.evaluate
g.evaluate = lambda *a,**k: original(*a,**k,signature_verifier=signature,runner_factory=Runner,checks_factory=lambda:(Check("probe"),Check("public-signatures")))
raise SystemExit(g.main())
''')
    hooks = repo / ".git/hooks"
    hook = hooks / "pre-push"
    hook.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, "-I", str(harness),
                    "--repo", str(repo), "--runtime", str(runtime), "--trust", str(trust)])
                    + ' --pre-push "$@"\n')
    hook.chmod(0o700)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-q", str(remote))
    return remote


def test_real_push_green_head_red_ref_is_blocked(repo, push_harness):
    green = git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "marker").write_text("red")
    red = commit(repo)
    git(repo, "checkout", "--detach", green)
    (repo / "owner-sentinel").write_text("unchanged")
    pushed = git(repo, "push", str(push_harness), f"{red}:refs/heads/red", check=False)
    assert pushed.returncode != 0 and '"code": "candidate_failed"' in pushed.stdout
    assert red in pushed.stdout
    assert git(push_harness, "show-ref", check=False).returncode != 0
    assert (repo / "owner-sentinel").read_text() == "unchanged"
    assert not (repo / "generated").exists()


def test_real_multiref_push_blocks_all_and_green_push_succeeds(repo, push_harness):
    green = git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "marker").write_text("red")
    red = commit(repo)
    result = git(repo, "push", str(push_harness), f"{green}:refs/heads/green",
                 f"{red}:refs/heads/red", check=False)
    assert result.returncode != 0
    assert git(push_harness, "show-ref", check=False).returncode != 0
    git(repo, "push", str(push_harness), f"{green}:refs/heads/green")
    assert git(push_harness, "rev-parse", "refs/heads/green").stdout.strip() == green


def test_real_push_bad_candidate_signature_is_blocked(repo, push_harness):
    (repo / "signature").write_text("invalid")
    red = commit(repo)
    result = git(repo, "push", str(push_harness), f"{red}:refs/heads/red", check=False)
    assert result.returncode != 0
    assert "candidate_signature_failed" in result.stdout
    assert git(push_harness, "show-ref", check=False).returncode != 0


def test_real_no_verify_bypasses_hook_explicit_limitation(repo, push_harness):
    (repo / "marker").write_text("red")
    red = commit(repo)
    result = git(repo, "push", "--no-verify", str(push_harness), f"{red}:refs/heads/bypass")
    assert "candidate_failed" not in result.stdout
    assert git(push_harness, "rev-parse", "refs/heads/bypass").stdout.strip() == red


# ————— بوابةُ النسبة: دفعةٌ مجهولةُ المُنتِج تُردّ ولو كانت خضراء —————

def test_untagged_commit_blocks_the_push_before_any_candidate_runs(repo):
    Runner.calls.clear()
    (repo / "added-by-an-anonymous-agent").write_text("green")
    untagged = commit(repo, agent=None)
    result = evaluate(repo, [untagged])
    assert result["status"] == "failed"
    assert result["code"] == "unattributed_push"
    assert result["attribution"]["code"] == "unattributed_commits"
    assert Runner.calls == [], "لا تُشغَّل شيفرةُ المرشَّح قبل ثبوت النسبة"


def test_unregistered_agent_blocks_the_push(repo):
    (repo / "added-by-a-new-agent").write_text("x")
    unregistered = commit(repo, agent="blackbox/agent-1")
    result = evaluate(repo, [unregistered])
    assert result["status"] == "failed"
    assert result["attribution"]["code"] == "unattributed_commits"


def test_missing_registry_fails_closed(repo):
    (repo / "registry/agents.json").unlink()
    result = evaluate(repo, [commit(repo, agent=None)])
    assert result["status"] == "failed"
    assert result["attribution"]["code"] == "attribution_registry_unavailable"


def test_tagged_commit_passes_attribution_and_reaches_the_checks(repo):
    Runner.calls.clear()
    (repo / "added-by-a-named-agent").write_text("x")
    result = evaluate(repo, [commit(repo)])
    assert result["status"] == "passed"
    assert result["attribution"]["status"] == "passed"
    assert Runner.calls, "الفحوص تُشغَّل بعد ثبوت النسبة"


def test_untagged_ancestor_in_the_pushed_range_is_caught_not_only_the_tip(repo):
    """المدى المدفوع كلُّه يُفحص: دفعةٌ مجهولةٌ تحت رأسٍ موسوم تُردّ."""
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "middle").write_text("مجهولة")
    commit(repo, agent=None)
    (repo / "top").write_text("موسومة")
    tip = commit(repo)
    updates = [{"local_oid": tip, "local_ref": "synthetic",
                "remote_ref": "refs/heads/main", "remote_oid": base}]
    result = gate.evaluate(repo, updates, RECEIPT, TRUST, signature_verifier=signature,
                           runner_factory=Runner, checks_factory=lambda: CHECKS)
    assert result["status"] == "failed"
    assert [f["code"] for f in result["attribution"]["findings"]] == ["agent_tag_missing"]


def test_gate_optional_evidence_is_external_and_preserves_failed_check(repo, tmp_path):
    class CapturingRunner(Runner):
        def __init__(self, receipt, *, evidence): self.evidence = evidence
        def run(self, root, check):
            with self.evidence.capture(check.name) as (stream, record):
                stream.write(b"synthetic private failure detail")
            return {"name":check.name,"code":"check_failed","exit_code":17,"evidence":record}
    updates=[{"local_oid":git(repo,"rev-parse","HEAD").stdout.strip(),"local_ref":"synthetic",
              "remote_ref":"refs/heads/main","remote_oid":"0"*40}]
    path = tmp_path.resolve() / "evidence"
    result=gate.evaluate(repo,updates,RECEIPT,TRUST,signature_verifier=signature,
        runner_factory=CapturingRunner,checks_factory=lambda:CHECKS,evidence_dir=path)
    assert result["status"]=="failed" and len(result["commits"][0]["checks"])==2
    assert result["commits"][0]["checks"][-1]["exit_code"]==17
    assert len(result["evidence"]["logs"])==1
    assert Path(result["evidence"]["logs"][0]["path"]).read_bytes()==b"synthetic private failure detail"
    assert "synthetic private failure detail" not in json.dumps(result)
    with pytest.raises(helper.GateError,match="trusted_path_inside_candidate"):
        gate.evaluate(repo,updates,RECEIPT,TRUST,signature_verifier=signature,
            runner_factory=CapturingRunner,checks_factory=lambda:CHECKS,evidence_dir=repo/"evidence")
    assert not (repo/"evidence").exists()
