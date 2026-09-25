"""No Docker calls: policy, snapshot and transport contracts use an explicit fake daemon.

These tests do not certify a live container boundary. That requires a separately
authorized integration run against the approved image receipt.
"""
from __future__ import annotations

import ast
import base64
import json
import os
from pathlib import Path

import pytest

from agent.builtin_tools import get_all_tools
from agent.actions import ActionStore
from agent.journal import Journal
from agent.registry import ToolContext, ToolRegistry
from core.contracts import ToolCall
from core import execution


@pytest.fixture(autouse=True)
def no_configured_backends(monkeypatch):
    monkeypatch.setattr(execution, "_BACKENDS", {})


@pytest.fixture
def configured(tmp_path):
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "script.py").write_text("print('candidate')\n", encoding="utf-8")
    (root / "unselected.txt").write_text("must not enter container", encoding="utf-8")
    receipt = tmp_path / "runtime.json"
    receipt.write_text(json.dumps({"schema_version": 1, "image_id": "sha256:" + "a" * 64,
        "lock_sha256": "b" * 64, "python_version": "3.14.7"}), encoding="utf-8")
    receipt.chmod(0o600)
    backend = execution.configure_execution_backend(receipt, root, ("script.py",))
    return backend, receipt, root


class FakeDocker:
    def __init__(self, backend):
        self.backend = backend
        self.calls = []
        self.container_id = "c" * 64
        self.name = None
        self.driver = None
        self.mutate_image = lambda value: None
        self.mutate_container = lambda value: None
        self.start_error = None
        self.cleanup_ok = True
        self.started = False
        self.exit_code = 0
        self.stdout = b"candidate\n"
        self.stderr = b""
        self.preflight_exit_code = 0
        self.preflight_stdout = b""

    def container(self):
        return {"Id": self.container_id, "Name": "/" + self.name,
            "Image": self.backend.receipt["image_id"],
            "State": {"Running": False, "Status": "exited" if self.started else "created",
                      "ExitCode": self.preflight_exit_code if self.driver == execution._RUNTIME_PREFLIGHT else self.exit_code,
                      "Error": "", "OOMKilled": False},
            "Config": {"User": "1000:1000", "Entrypoint": ["/opt/venv/bin/python"],
                       "Cmd": ["-I", "-c", self.driver], "WorkingDir": "/workspace"},
            "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
                "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"], "PidsLimit": 128,
                "Memory": execution.MEMORY_BYTES, "MemorySwap": execution.MEMORY_BYTES,
                "NanoCpus": 2_000_000_000, "IpcMode": "private", "CgroupnsMode": "private",
                "LogConfig": {"Type": "none"},
                "Tmpfs": dict(execution.TMPFS)}, "Mounts": []}

    def __call__(self, *args, data=b"", timeout=20):
        self.calls.append((args, data, timeout))
        if args[:2] == ("image", "inspect"):
            image = {"Id": self.backend.receipt["image_id"], "Os": "linux", "Config": {
                "Labels": {"diwan.lock-sha256": self.backend.receipt["lock_sha256"]}}}
            self.mutate_image(image)
            return 0, json.dumps([image]).encode(), b""
        if args[0] == "create":
            self.name = args[args.index("--name") + 1]
            self.driver = args[-1]
            self.started = False
            return 0, self.container_id.encode(), b""
        if args[0] == "inspect":
            value = self.container()
            self.mutate_container(value)
            return 0, json.dumps([value]).encode(), b""
        if args[0] == "start":
            if self.driver == execution._RUNTIME_PREFLIGHT:
                self.started = True
                return self.preflight_exit_code, self.preflight_stdout, b""
            if self.start_error:
                raise self.start_error
            self.started = True
            return self.exit_code, self.stdout, self.stderr
        if args[0] == "rm":
            return (0 if self.cleanup_ok else 1), b"", b""
        if args[0] == "ps":
            return 1, b"", b"daemon unavailable"
        raise AssertionError(args)


@pytest.fixture
def fake_daemon(configured, monkeypatch):
    backend, receipt, root = configured
    fake = FakeDocker(backend)
    monkeypatch.setattr(backend, "_docker", fake)
    return backend, fake, root


@pytest.mark.parametrize("name,arguments", [
    ("run_command", {"argv": ["/bin/sh", "-c", "touch never-created"]}),
    ("execute_isolated_command", {"command": "touch never-created"}),
    ("run_tests", {"paths": ["tests/test_candidate.py"]}),
])
def test_all_candidate_entrypoints_refuse_environment_declarations(tmp_path, monkeypatch, name, arguments):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_candidate.py").write_text("raise RuntimeError('must not run')\n")
    monkeypatch.setenv("DIWAN_DISPOSABLE_HOST", "asserted-but-not-verified")
    monkeypatch.setenv("DIWAN_ISOLATED_RUNNER", "1")
    def unexpected_subprocess(*args, **kwargs):
        pytest.fail("unconfigured execution must not contact Docker or launch a host process")
    monkeypatch.setattr(execution.subprocess, "Popen", unexpected_subprocess)
    ctx = ToolContext(tmp_path, Journal(tmp_path), approved_call_ids=frozenset({"approved"}),
                      disposable_host="asserted-but-not-verified")
    registry = ToolRegistry(*get_all_tools())
    store = ActionStore(tmp_path.parent / (tmp_path.name + "-actions"), tmp_path)
    call = ToolCall("approved", name, arguments)
    position = dict(session_id="s", turn_id="t", step_index=0, request_digest="a" * 64)
    store.register_step(**position, calls=(call,), specs=registry.specs())
    result = registry.invoke_prepared(call, ctx, store=store, **position, call_index=0)
    assert result["status"] == "refused"
    assert result["code"] == "execution_backend_unavailable"
    assert not (tmp_path / "never-created").exists()


def test_only_explicit_snapshot_files_and_argv_are_sent_after_inspection(fake_daemon):
    backend, fake, root = fake_daemon
    result = execution.execute_candidate(("/bin/sh", "-c", "echo candidate; touch inside"), root)
    assert result.boundary == "docker:" + fake.container_id
    assert result.stdout == "candidate\n"
    assert [call[0][0] for call in fake.calls] == ["image", "create", "inspect", "start", "inspect", "rm",
                                               "create", "inspect", "start", "inspect", "rm"]
    create_args = fake.calls[1][0]
    assert "--pull=never" in create_args and "--network=none" in create_args
    assert not any(arg.startswith(("--mount", "--volume", "--env", "--privileged")) for arg in create_args)
    assert "echo candidate; touch inside" not in create_args
    assert json.loads(fake.calls[3][1]) == {"runtime": backend.receipt}
    payload = json.loads(fake.calls[8][1])
    assert payload["argv"] == ["/bin/sh", "-c", "echo candidate; touch inside"]
    assert [item["path"] for item in payload["files"]] == ["script.py"]
    assert base64.b64decode(payload["files"][0]["data"]) == (root / "script.py").read_bytes()
    assert "unselected.txt" not in str(payload)
    assert not (root / "inside").exists()


@pytest.mark.parametrize("field,value", [
    ("NetworkMode", "bridge"), ("ReadonlyRootfs", False), ("Privileged", True),
    ("CapDrop", []), ("CapAdd", ["SYS_ADMIN"]), ("SecurityOpt", ["seccomp=unconfined"]),
    ("PidsLimit", 0), ("Memory", 0), ("MemorySwap", -1), ("NanoCpus", 0),
    ("PidMode", "host"), ("IpcMode", "host"), ("UTSMode", "host"), ("UsernsMode", "host"),
    ("CgroupnsMode", "host"), ("Binds", ["/:/host"]), ("Tmpfs", {}),
    ("LogConfig", {"Type": "json-file"}),
    ("Devices", [{"PathOnHost": "/dev/disk0"}]), ("VolumesFrom", ["owner-data"]),
])
def test_unverified_container_controls_block_candidate_input_and_clean_up(fake_daemon, field, value):
    backend, fake, root = fake_daemon
    fake.mutate_container = lambda item: item["HostConfig"].update({field: value})
    with pytest.raises(execution.ExecutionRefused) as exc:
        backend.run(("/bin/true",), timeout_s=5)
    assert exc.value.code == "execution_boundary_unverified"
    assert "start" not in [call[0][0] for call in fake.calls]
    assert fake.calls[-1][0] == ("rm", "--force", fake.name)


@pytest.mark.parametrize("change", [
    lambda item: item["Config"].update(User="0"),
    lambda item: item["Config"].update(Entrypoint=["/bin/sh"]),
    lambda item: item["Mounts"].append({"Type": "bind", "Destination": "/var/run/docker.sock"}),
])
def test_container_user_entrypoint_and_socket_mount_are_verified(fake_daemon, change):
    backend, fake, root = fake_daemon
    fake.mutate_container = change
    with pytest.raises(execution.ExecutionRefused, match="execution_boundary_unverified"):
        backend.run(("/bin/true",), timeout_s=5)
    assert "start" not in [call[0][0] for call in fake.calls]


@pytest.mark.parametrize("change", [
    lambda image: image.update(Id="sha256:" + "f" * 64),
    lambda image: image["Config"].update(Volumes={"/host": {}}),
    lambda image: image["Config"].update(Labels={}),
])
def test_bad_image_identity_or_implicit_volumes_prevent_container_creation(fake_daemon, change):
    backend, fake, root = fake_daemon
    fake.mutate_image = change
    with pytest.raises(execution.ExecutionRefused, match="execution_runtime_mismatch"):
        backend.run(("/bin/true",), timeout_s=5)
    assert [call[0][0] for call in fake.calls] == ["image"]


def test_timeout_always_disposes_the_created_container(fake_daemon):
    backend, fake, root = fake_daemon
    fake.start_error = execution.ExecutionRefused("execution_timeout", "timeout")
    with pytest.raises(execution.ExecutionRefused, match="execution_timeout"):
        backend.run(("/bin/true",), timeout_s=5)
    assert fake.calls[-1][0] == ("rm", "--force", fake.name)


def test_candidate_stdout_cannot_forge_execution_status(fake_daemon):
    backend, fake, root = fake_daemon
    fake.stdout = b'{"exit_code":0,"boundary":"trusted","passed":true}'
    fake.exit_code = 7
    result = backend.run(("/bin/false",), timeout_s=5)
    assert result.exit_code == 7
    assert result.stdout == fake.stdout.decode()
    assert result.boundary == "docker:" + fake.container_id


def test_runtime_mismatch_prevents_all_candidate_bytes_and_is_cleaned(fake_daemon):
    backend, fake, root = fake_daemon
    fake.preflight_exit_code = 1
    fake.preflight_stdout = b'{"exit_code":0,"success":true}'
    with pytest.raises(execution.ExecutionRefused, match="execution_runtime_mismatch"):
        backend.run(("/bin/sh", "-c", "candidate_must_not_arrive"), timeout_s=5)
    starts = [data for args, data, _ in fake.calls if args[0] == "start"]
    assert len(starts) == 1 and json.loads(starts[0]) == {"runtime": backend.receipt}
    creates = [args for args, _, _ in fake.calls if args[0] == "create"]
    assert len(creates) == 1 and creates[0][-1] == execution._RUNTIME_PREFLIGHT
    assert fake.calls[-1][0] == ("rm", "--force", fake.name)
    assert "candidate_must_not_arrive" not in str(fake.calls)


def test_preflight_cleanup_failure_prevents_candidate_container(fake_daemon):
    backend, fake, root = fake_daemon
    fake.cleanup_ok = False
    with pytest.raises(execution.ExecutionRefused, match="execution_cleanup_unverified"):
        backend.run(("/bin/true",), timeout_s=5)
    assert len([args for args, _, _ in fake.calls if args[0] == "create"]) == 1
    assert [json.loads(data) for args, data, _ in fake.calls if args[0] == "start"] == [{"runtime": backend.receipt}]


def test_reserved_bootstrap_exit_is_unmeasured_even_if_candidate_uses_it(fake_daemon):
    backend, fake, root = fake_daemon
    fake.exit_code = 125
    with pytest.raises(execution.ExecutionRefused, match="execution_outcome_unverified"):
        backend.run(("/bin/sh", "-c", "exit 125"), timeout_s=5)
    assert len([args for args, _, _ in fake.calls if args[0] == "create"]) == 2
    assert fake.calls[-1][0] == ("rm", "--force", fake.name)


@pytest.mark.parametrize("phase,expected", [("runtime", "sandbox_runtime_mismatch"),
                                           ("setup", "sandbox_outcome_unverified")])
def test_pre_candidate_failures_are_not_model_failures_in_typed_report(configured, monkeypatch, tmp_path, phase, expected):
    from types import SimpleNamespace
    from core import sandbox
    from core.contracts import Response, Usage
    from evaluation.capabilities import evaluate_suite
    from evaluation.human_review import validate_report
    _, receipt, root = configured
    backend = execution.DockerExecutionBackend(receipt, root, ())
    fake = FakeDocker(backend)
    if phase == "runtime":
        fake.preflight_exit_code = 1
    else:
        fake.exit_code = 125
    monkeypatch.setattr(backend, "_docker", fake)
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", backend)
    provider = SimpleNamespace(model="synthetic", is_local=True, estimate_micros=lambda request: 0,
        complete=lambda request: Response("x=1", Usage(1, 1), "complete", 0,
                                          provider="synthetic", model_version="synthetic"))
    suite = {"schema_version": 1, "suite_id": "preflight-test", "split": "development",
        "description": "synthetic only", "cases": [{"case_id": "case-1", "capability": "code",
        "messages": [{"role": "user", "content": "x=1"}], "reference": "x=1", "rubric": ["correct"],
        "checks": [{"kind": "python_sandbox", "value": "assert x == 1"}], "critical": False}]}
    report = evaluate_suite(suite, provider, tmp_path / "reports")
    assert report["results"][0]["error_code"] == expected
    assert report["results"][0]["checks_status"] == "not_run"
    assert report["results"][0]["checks"] == []
    assert report["summary"]["automatic_failures"] == report["summary"]["automatic_passes"] == 0
    assert report["summary"]["execution_errors"] == 1
    assert validate_report(report) is report


def test_running_children_cannot_be_reported_as_completed(fake_daemon):
    backend, fake, root = fake_daemon
    def mutate_after_start(value):
        if fake.started:
            value["State"].update(Running=True, Status="running", ExitCode=0)
    fake.mutate_container = mutate_after_start
    with pytest.raises(execution.ExecutionRefused, match="execution_exit_unverified"):
        backend.run(("/bin/true",), timeout_s=5)
    assert fake.calls[-1][0] == ("rm", "--force", fake.name)


def test_unverified_cleanup_never_returns_success(fake_daemon):
    backend, fake, root = fake_daemon
    fake.cleanup_ok = False
    with pytest.raises(execution.ExecutionRefused, match="execution_cleanup_unverified"):
        backend.run(("/bin/true",), timeout_s=5)


def test_receipt_must_be_private_and_outside_candidate(configured):
    backend, receipt, root = configured
    receipt.chmod(0o644)
    with pytest.raises(execution.ExecutionRefused, match="execution_receipt_untrusted"):
        execution.DockerExecutionBackend(receipt, root, ())
    candidate_receipt = root / "runtime.json"
    candidate_receipt.write_bytes(receipt.read_bytes())
    candidate_receipt.chmod(0o600)
    with pytest.raises(execution.ExecutionRefused, match="execution_receipt_untrusted"):
        execution.DockerExecutionBackend(candidate_receipt, root, ())


@pytest.mark.parametrize("name", [".", "..", ".env", ".git/config", "../outside.txt", "/etc/passwd", "a/../b",
                                  "secrets/token.txt", "private/seed", "credentials/token.json", "key.pem"])
def test_snapshot_refuses_credential_and_escaping_paths(configured, name):
    backend, receipt, root = configured
    with pytest.raises(execution.ExecutionRefused):
        execution.DockerExecutionBackend(receipt, root, (name,))


@pytest.mark.parametrize("kind", ["file_link", "directory_link", "hard_link", "fifo"])
def test_snapshot_never_follows_links_or_reads_special_files(configured, tmp_path, kind):
    backend, receipt, root = configured
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "value").write_text("outside")
    if kind == "file_link":
        (root / "input").symlink_to(outside / "value")
        selected = "input"
    elif kind == "directory_link":
        (root / "input").symlink_to(outside, target_is_directory=True)
        selected = "input/value"
    elif kind == "hard_link":
        os.link(outside / "value", root / "input")
        selected = "input"
    else:
        os.mkfifo(root / "input")
        selected = "input"
    probe = execution.DockerExecutionBackend(receipt, root, (selected,))
    with pytest.raises(execution.ExecutionRefused, match="execution_snapshot_invalid"):
        probe._snapshot()


@pytest.mark.parametrize("kind", ["leaf_link", "ancestor_link", "hard_link"])
def test_receipt_is_read_through_one_unlinked_descriptor(configured, tmp_path, kind):
    backend, receipt, root = configured
    if kind == "leaf_link":
        alias = tmp_path / "alias.json"
        alias.symlink_to(receipt)
    elif kind == "hard_link":
        alias = tmp_path / "alias.json"
        os.link(receipt, alias)
    else:
        private = tmp_path / "private-runtime"
        private.mkdir()
        receipt.rename(private / "runtime.json")
        alias_parent = tmp_path / "alias-parent"
        alias_parent.symlink_to(private, target_is_directory=True)
        alias = alias_parent / "runtime.json"
    with pytest.raises(execution.ExecutionRefused):
        execution.DockerExecutionBackend(alias, root, ())


def test_snapshot_rejects_workspace_replacement_after_configuration(configured):
    backend, receipt, root = configured
    root.rename(root.with_name("original"))
    root.mkdir()
    (root / "script.py").write_text("replacement")
    with pytest.raises(execution.ExecutionRefused, match="execution_workspace_changed"):
        backend._snapshot()


def test_workspace_ancestor_cannot_be_replaced_by_a_symlink(configured, tmp_path):
    backend, receipt, root = configured
    parent = tmp_path / "source-parent"
    parent.mkdir()
    nested = parent / "workspace"
    nested.mkdir()
    (nested / "probe.py").write_text("print('original')")
    probe = execution.DockerExecutionBackend(receipt, nested, ("probe.py",))
    renamed = tmp_path / "renamed-parent"
    parent.rename(renamed)
    parent.symlink_to(renamed, target_is_directory=True)
    with pytest.raises(execution.ExecutionRefused, match="execution_workspace_invalid"):
        probe._snapshot()
    with pytest.raises(execution.ExecutionRefused, match="execution_workspace_invalid"):
        execution.DockerExecutionBackend(receipt, nested, ())


def test_backend_cannot_be_reused_for_a_different_workspace(fake_daemon, tmp_path):
    backend, fake, root = fake_daemon
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(execution.ExecutionRefused, match="execution_backend_unavailable"):
        execution.execute_candidate(("/bin/true",), other)
    assert fake.calls == []


@pytest.mark.parametrize("timeout", [None, True, "30", 0, -1, 901, 10 ** 400, float("inf"), float("nan")])
def test_invalid_timeouts_are_named_refusals_before_contacting_docker(fake_daemon, timeout):
    backend, fake, root = fake_daemon
    with pytest.raises(execution.ExecutionRefused, match="execution_arguments_invalid"):
        backend.run(("/bin/true",), timeout_s=timeout)
    assert fake.calls == []


def test_host_credentials_and_docker_routing_are_not_forwarded(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted.invalid")
    monkeypatch.setenv("DOCKER_CONFIG", "/owner/docker-credentials")
    monkeypatch.setenv("SECRET_TOKEN", "synthetic-secret")
    assert set(execution._clean_env()) == {"PATH", "LANG", "LC_ALL"}
    assert "synthetic-secret" not in str(execution._clean_env())


def test_all_tool_adapters_use_the_configured_container(fake_daemon):
    backend, fake, root = fake_daemon
    (root / "tests").mkdir()
    (root / "tests/test_example.py").write_text("def test_example(): pass\n")
    ctx = ToolContext(root, Journal(root), approved_call_ids=frozenset({"approved"}))
    registry = ToolRegistry(*get_all_tools())
    store = ActionStore(root.parent / "actions", root)
    for index, (name, arguments, expected) in enumerate([
        ("run_command", {"argv": ["/bin/echo", "hello"]}, ["/bin/echo", "hello"]),
        ("execute_isolated_command", {"command": "echo hello"}, ["/bin/sh", "-c", "echo hello"]),
        ("run_tests", {"paths": ["tests/test_example.py"]},
         ["/opt/venv/bin/python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_example.py"]),
    ]):
        fake.calls.clear()
        fake.started = False
        call = ToolCall("approved", name, arguments)
        position = dict(session_id="s", turn_id="t", step_index=index, request_digest="a" * 64)
        store.register_step(**position, calls=(call,), specs=registry.specs())
        result = registry.invoke_prepared(call, ctx, store=store, **position, call_index=0)
        if result["status"] == "awaiting_owner":
            store.decide(result["action_id"], result["call_digest"], result["revision"], approve=True)
            result = registry.invoke_prepared(call, ctx, store=store, **position, call_index=0)
        assert result["status"] == "ok", result
        sent = [data for args, data, _ in fake.calls if args[0] == "start"][-1]
        assert json.loads(sent)["argv"] == expected


def test_driver_is_valid_python_and_no_candidate_host_shell_exists():
    ast.parse(execution._RUNTIME_PREFLIGHT)
    ast.parse(execution._CONTAINER_DRIVER)
    root = Path(__file__).resolve().parent.parent
    for filename in ("agent/builtin_tools.py", "core/tools_registry.py"):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            assert not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess")
