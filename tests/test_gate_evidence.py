"""Private gate diagnostics with a fake Docker client; no daemon or host hooks."""
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from tools import gate_helpers as helper

RECEIPT = {"image_id":"sha256:"+"1"*64, "lock_sha256":"2"*64,
           "python_version":"3.14.7", "node_major":24}
RAW = b"candidate failure\nSENSITIVE_FIXTURE_OUTPUT\n\x00binary\xff"


class Docker:
    def __init__(self, *, code=1, timeout=False, cleanup=False, after=None, probe_code=0):
        self.code, self.timeout, self.cleanup = code, timeout, cleanup
        self.after, self.probe_code, self.calls = after, probe_code, []
    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(argv,0,json.dumps([{"Id":RECEIPT["image_id"],
                "Config":{"Labels":{"diwan.lock-sha256":RECEIPT["lock_sha256"]}}}]).encode())
        if argv[1] == "run":
            candidate = "--mount" in argv
            kwargs["stdout"].write(RAW if candidate else b"runtime probe\n")
            kwargs["stdout"].flush()
            assert kwargs["stdout"] is kwargs["stderr"]
            if candidate and self.after: self.after()
            if candidate and self.timeout: raise subprocess.TimeoutExpired(argv,1)
            return subprocess.CompletedProcess(argv,self.code if candidate else self.probe_code)
        if argv[1] == "rm": return subprocess.CompletedProcess(argv,int(self.cleanup))
        if argv[1] == "ps": return subprocess.CompletedProcess(argv,0,"still-present" if self.cleanup else "")
        raise AssertionError(argv)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    candidate = root / "candidate"; candidate.mkdir()
    evidence = helper.EvidenceStore(root / "private" / "evidence", candidate)
    def runner(docker):
        monkeypatch.setattr(helper.shutil,"which",lambda *a,**k:"/trusted/docker")
        monkeypatch.setattr(helper.subprocess,"run",docker)
        return helper.DockerRunner(RECEIPT,evidence=evidence)
    return candidate,evidence,runner


def check(): return SimpleNamespace(name="pytest", argv=("/opt/venv/bin/python","-m","pytest"), timeout_s=1)


def test_failure_output_retained_private_and_never_embedded_in_report(fixture):
    candidate,evidence,create = fixture; docker = Docker(code=7)
    result = create(docker).run(candidate,check())
    assert result["code"] == "check_failed" and result["process_exit_code"] == result["exit_code"] == 7
    capture = result["evidence"]; path = Path(capture["path"])
    assert capture["status"] == "retained" and path.read_bytes() == RAW
    assert capture["sha256"] == helper.digest(RAW) and capture["bytes"] == len(RAW)
    assert path.stat().st_mode & 0o777 == 0o600 and evidence.path.stat().st_mode & 0o777 == 0o700
    assert len(evidence.records) == 2 and evidence.records[0]["check"] == "runtime-identity"
    assert "SENSITIVE_FIXTURE_OUTPUT" not in json.dumps(result)
    command = next(c for c in docker.calls if "--mount" in c)
    assert "--network=none" in command and "--read-only" in command and "--cap-drop=ALL" in command
    assert command.count("--mount") == 1 and str(evidence.path) not in " ".join(command)
    assert not any("docker.sock" in p for p in command)


@pytest.mark.parametrize("cleanup", [False, True])
def test_timeout_keeps_raw_output_and_does_not_become_pass(fixture, cleanup):
    candidate,evidence,create = fixture; docker = Docker(timeout=True)
    runner = create(docker); docker.cleanup = cleanup
    result = runner.run(candidate,check())
    assert result["process_code"] == "check_timeout" and result["process_exit_code"] is None
    assert result["code"] == ("runtime_cleanup_failed" if cleanup else "check_timeout")
    assert result["exit_code"] == (125 if cleanup else 124)
    assert result["cleanup_code"] == ("runtime_cleanup_failed" if cleanup else "passed")
    assert Path(result["evidence"]["path"]).read_bytes() == RAW
    assert any(c[1:3] == ["rm","--force"] for c in docker.calls)


def test_cleanup_failure_preserves_original_nonzero_exit(fixture):
    candidate,evidence,create = fixture
    docker = Docker(code=17)
    runner = create(docker); docker.cleanup = True
    result = runner.run(candidate,check())
    assert result["code"] == result["cleanup_code"] == "runtime_cleanup_failed"
    assert result["process_code"] == "check_failed" and result["process_exit_code"] == 17
    assert result["exit_code"] == 125 and result["evidence"]["status"] == "retained"


def test_logging_failure_cannot_turn_a_successful_process_into_pass(fixture, monkeypatch):
    candidate,evidence,create = fixture; runner = create(Docker(code=0))
    original = evidence._check; checks = []
    def failure(fd):
        checks.append(True)
        if len(checks) == 2: raise OSError("synthetic final evidence fsync/identity failure")
        return original(fd)
    monkeypatch.setattr(evidence,"_check",failure)
    result = runner.run(candidate,check())
    assert result["process_exit_code"] == 0 and result["process_code"] == "passed"
    assert result["code"] == "evidence_capture_failed" and result["exit_code"] == 125
    assert result["evidence"]["status"] == "failed" and "sha256" not in result["evidence"]


def test_directory_fsync_failure_records_failed_evidence_before_execution(fixture, monkeypatch):
    candidate,evidence,create = fixture; docker = Docker(code=0); runner = create(docker)
    def failure(_): raise OSError("synthetic directory fsync failure")
    monkeypatch.setattr(helper.os,"fsync",failure)
    count = len(docker.calls)
    result = runner.run(candidate,check())
    assert result["code"] == "evidence_open_failed" and result["process_code"] == "not_started"
    assert len(docker.calls) == count and evidence.records[-1]["status"] == "failed"
    assert "sha256" not in evidence.records[-1]


def test_unique_output_collision_never_overwrites_or_executes(fixture, monkeypatch):
    candidate,evidence,create = fixture; docker = Docker(code=0); runner = create(docker)
    monkeypatch.setattr(helper.uuid,"uuid4",lambda:SimpleNamespace(hex="a"*32))
    first = runner.run(candidate,check()); original = Path(first["evidence"]["path"]).read_bytes()
    calls = len(docker.calls)
    second = runner.run(candidate,check())
    assert second["code"] == "evidence_open_failed" and second["process_code"] == "not_started"
    assert len(docker.calls) == calls and Path(first["evidence"]["path"]).read_bytes() == original


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_link_log_collision_never_follows_target(fixture, monkeypatch, tmp_path, kind):
    candidate,evidence,create = fixture; docker = Docker(code=0); runner = create(docker)
    monkeypatch.setattr(helper.uuid,"uuid4",lambda:SimpleNamespace(hex="a"*32))
    outside = tmp_path / "owner"; outside.write_bytes(b"owner bytes")
    linked = evidence.path / ("pytest-"+"a"*32+".log")
    if kind == "symlink": linked.symlink_to(outside)
    else: os.link(outside, linked)
    calls = len(docker.calls); result = runner.run(candidate,check())
    assert result["code"] == "evidence_open_failed" and len(docker.calls) == calls
    assert outside.read_bytes() == b"owner bytes"


def test_evidence_inside_candidate_refused_before_directory_creation(tmp_path):
    candidate = tmp_path.resolve() / "candidate"; candidate.mkdir()
    path = candidate / "private"
    with pytest.raises(helper.GateError,match="trusted_path_inside_candidate"):
        helper.EvidenceStore(path,candidate)
    assert not path.exists()


def test_symlink_parent_and_leaf_are_refused(tmp_path):
    root = tmp_path.resolve(); candidate = root / "candidate"; candidate.mkdir()
    actual = root / "owner"; actual.mkdir(mode=0o700)
    link = root / "alias"; link.symlink_to(actual,target_is_directory=True)
    for path in (link,link / "evidence"):
        with pytest.raises(helper.GateError,match="evidence_path_unsafe"):
            helper.EvidenceStore(path,candidate)
    assert list(actual.iterdir()) == []


def test_parent_traversal_refused(tmp_path):
    root = tmp_path.resolve(); candidate = root / "candidate"; candidate.mkdir()
    with pytest.raises(helper.GateError,match="evidence_path_invalid"):
        helper.EvidenceStore(root / "unused" / ".." / "evidence",candidate)
    assert not (root / "unused").exists()


def test_existing_nonprivate_directory_is_refused_not_chmodded(tmp_path):
    root = tmp_path.resolve(); candidate = root / "candidate"; candidate.mkdir()
    path = root / "evidence"; path.mkdir(mode=0o755)
    with pytest.raises(helper.GateError,match="evidence_permissions_invalid"):
        helper.EvidenceStore(path,candidate)
    assert path.stat().st_mode & 0o777 == 0o755


def test_case_alias_of_existing_directory_is_refused_where_supported(tmp_path):
    root = tmp_path.resolve(); candidate = root / "candidate"; candidate.mkdir()
    actual = root / "PrivateEvidence"; actual.mkdir(mode=0o700)
    alternate = root / "privateevidence"
    if not alternate.exists(): pytest.skip("filesystem has case-sensitive names")
    with pytest.raises(helper.GateError,match="evidence_path_alias"):
        helper.EvidenceStore(alternate,candidate)
    assert list(actual.iterdir()) == []


def test_replaced_evidence_root_refused_before_candidate_execution(fixture):
    candidate,evidence,create = fixture; docker = Docker(code=0); runner = create(docker)
    old = evidence.path.with_name("preserved"); evidence.path.rename(old)
    evidence.path.mkdir(mode=0o700); count = len(docker.calls)
    result = runner.run(candidate,check())
    assert result["code"] == "evidence_directory_changed" and len(docker.calls) == count
    assert list(evidence.path.iterdir()) == []


def test_runtime_probe_still_blocks_and_retains_its_failure(fixture):
    _,evidence,create = fixture
    with pytest.raises(helper.GateError,match="runtime_probe_failed"):
        create(Docker(probe_code=19))
    assert len(evidence.records) == 1
    assert evidence.records[0]["process_exit_code"] == 19
    assert evidence.records[0]["check_code"] == "check_failed"
    assert Path(evidence.records[0]["path"]).read_bytes() == b"runtime probe\n"
