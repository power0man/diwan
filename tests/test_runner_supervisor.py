"""Fresh worker lifecycle and token boundaries with fake subprocesses only."""
import io
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ci import run_persistent_runner as supervisor
from ci import start_isolated_runner as bootstrap
from ci import runner_entrypoint as entry

REPO = "https://github.com/example/project"
NAME = "diwan-isolated-0123456789abcdef"
TOKEN = "SYNTHETIC_REGISTRATION_TOKEN_DO_NOT_LOG"


@pytest.fixture
def receipt(tmp_path):
    value = {"schema_version": 1, "image_id": "sha256:" + "a" * 64,
             "runner_version": "2.337.0", "runner_mode": "ephemeral-only-v1",
             "lock_sha256": "b" * 64}
    path = tmp_path / "runner.json"
    path.write_text(json.dumps(value))
    return path


class Stop:
    def __init__(self):
        self.stopped = False
        self.waits = []
    def is_set(self):
        return self.stopped
    def set(self):
        self.stopped = True
    def wait(self, delay):
        self.waits.append(delay)
        return self.stopped


def test_two_jobs_get_two_names_tokens_and_completed_cleanup_before_next_registration(receipt, tmp_path, monkeypatch):
    state = tmp_path / "private"
    events = []
    def token(repo):
        assert not list(state.glob("*.active.json"))
        value = "synthetic-" + str(len(events))
        events.append(("registration", value))
        return value
    def worker(receipt, repo, token, name, **kwargs):
        active = list(state.glob("*.active.json"))
        assert len(active) == 1
        record = json.loads(active[0].read_text())
        assert record["worker_name"] == name
        assert token not in active[0].read_text()
        events.append(("worker", token, name))
        return 0  # Trusted lifecycle helper returns only after confirmed disposal.
    monkeypatch.setattr(supervisor, "get_registration_token", token)
    monkeypatch.setattr(supervisor, "run_one_worker", worker)
    stop = Stop()
    assert supervisor.run_persistent_runner(receipt, REPO, state_dir=state, max_workers=2, stop=stop) == 0
    assert [e[0] for e in events] == ["registration", "worker", "registration", "worker"]
    assert events[1][2] != events[3][2]
    assert events[1][1] != events[3][1]
    assert stop.waits == [1]
    assert not list(state.glob("*.active.json"))


def test_unknown_cleanup_blocks_replacement_and_restart(receipt, tmp_path, monkeypatch):
    state = tmp_path / "private"
    registrations = []
    monkeypatch.setattr(supervisor, "get_registration_token", lambda repo: registrations.append(repo) or TOKEN)
    monkeypatch.setattr(supervisor, "run_one_worker", lambda *a, **k: bootstrap.CLEANUP_UNVERIFIED)
    assert supervisor.run_persistent_runner(receipt, REPO, state_dir=state, stop=Stop()) == 3
    assert len(registrations) == 1
    assert len(list(state.glob("*.active.json"))) == 1
    with pytest.raises(supervisor.SupervisorRefused, match="previous_worker_cleanup_required"):
        supervisor.run_persistent_runner(receipt, REPO, state_dir=state, stop=Stop())
    assert len(registrations) == 1


def test_lock_blocks_a_second_supervisor_before_registration(receipt, tmp_path, monkeypatch):
    state = tmp_path / "private"
    monkeypatch.setattr(supervisor, "get_registration_token", lambda *a: pytest.fail("second supervisor registered"))
    with supervisor.supervisor_lock(state, REPO):
        with pytest.raises(supervisor.SupervisorRefused, match="supervisor_already_running"):
            supervisor.run_persistent_runner(receipt, REPO, state_dir=state, stop=Stop())


def test_failure_budget_and_exponential_backoff_are_bounded(receipt, tmp_path, monkeypatch):
    attempts = []
    def fail(repo):
        attempts.append(repo)
        raise supervisor.SupervisorRefused("registration_request_failed")
    monkeypatch.setattr(supervisor, "get_registration_token", fail)
    stop = Stop()
    assert supervisor.run_persistent_runner(receipt, REPO, state_dir=tmp_path / "private",
        max_failures=4, backoff=5, max_backoff=12, stop=stop) == 1
    assert len(attempts) == 4
    assert stop.waits == [5, 10, 12]


def test_worker_failure_uses_a_new_token_not_a_replayed_job(receipt, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(supervisor, "get_registration_token", lambda repo: "new-" + str(len(seen)))
    def worker(receipt, repo, token, name, **kwargs):
        seen.append((token, name))
        return 1
    monkeypatch.setattr(supervisor, "run_one_worker", worker)
    assert supervisor.run_persistent_runner(receipt, REPO, state_dir=tmp_path / "private",
        max_failures=2, stop=Stop()) == 1
    assert seen[0][0] != seen[1][0] and seen[0][1] != seen[1][1]


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "invalid_json", "invalid_token"])
def test_registration_failure_never_exposes_api_token_or_output(monkeypatch, capsys, failure):
    monkeypatch.setattr(supervisor.shutil, "which", lambda value: "/trusted/gh")
    def api(argv, **kwargs):
        assert argv == ["gh", "api", "--method", "POST", "repos/example/project/actions/runners/registration-token"]
        assert kwargs["capture_output"] and kwargs["timeout"] == 30
        assert not kwargs["check"]
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 30, output=TOKEN, stderr=TOKEN)
        raw = json.dumps({"token": TOKEN})
        if failure == "invalid_json": raw = TOKEN
        if failure == "invalid_token": raw = json.dumps({"token": TOKEN + "\n"})
        return subprocess.CompletedProcess(argv, 1 if failure == "nonzero" else 0, raw, TOKEN)
    monkeypatch.setattr(supervisor.subprocess, "run", api)
    with pytest.raises(supervisor.SupervisorRefused, match="registration_request_failed") as exc:
        supervisor.get_registration_token(REPO)
    assert TOKEN not in str(exc.value)
    assert TOKEN not in str(capsys.readouterr())


def test_supervisor_sends_token_once_via_stdin_and_rechecks_disposal(receipt, monkeypatch):
    inputs = []
    disposals = []
    class Process:
        returncode = 0
        def communicate(self, *, input, timeout):
            inputs.append(input)
            if len(inputs) == 1:
                raise subprocess.TimeoutExpired("bootstrap", 1)
    def popen(argv, **kwargs):
        assert TOKEN not in repr(argv) + repr(kwargs)
        assert "--persistent" not in argv
        assert argv[argv.index("--name") + 1] == NAME
        assert argv[argv.index("--expected-receipt-sha256") + 1] == hashlib.sha256(receipt.read_bytes()).hexdigest()
        return Process()
    monkeypatch.setattr(supervisor.subprocess, "Popen", popen)
    monkeypatch.setattr(supervisor, "dispose_container", lambda name: disposals.append(name) or True)
    assert supervisor.run_one_worker(receipt, REPO, TOKEN, NAME, timeout=60,
                                     receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(), label="diwan-isolated", stop=Stop()) == 0
    assert inputs == [(TOKEN + "\n").encode(), None]
    assert disposals == [NAME]


def test_even_successful_child_cannot_allow_next_worker_without_cleanup_proof(receipt, monkeypatch):
    process = SimpleNamespace(returncode=0, communicate=lambda **kwargs: None)
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(supervisor, "dispose_container", lambda name: False)
    assert supervisor.run_one_worker(receipt, REPO, TOKEN, NAME, timeout=60,
                                     receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(), label="diwan-isolated", stop=Stop()) == 3


def test_stop_signal_terminates_bootstrap_and_waits_for_cleanup(receipt, monkeypatch):
    events = []
    class Process:
        returncode = 143
        def terminate(self): events.append("terminate")
        def communicate(self, **kwargs): events.append("wait")
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(supervisor, "dispose_container", lambda name: events.append("cleanup") or True)
    stop = Stop(); stop.set()
    assert supervisor.run_one_worker(receipt, REPO, TOKEN, NAME, timeout=60,
                                     receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(), label="diwan-isolated", stop=stop) == 143
    assert events == ["terminate", "wait", "cleanup"]


@pytest.mark.parametrize("outcome,code", [("success", 0), ("failure", 1), ("timeout", 124), ("term", 143), ("spawn_failure", 1)])
def test_bootstrap_disposes_its_unique_container_on_every_exit(receipt, monkeypatch, capsys, outcome, code):
    calls = []
    class Process:
        returncode = 1 if outcome == "failure" else 0
        def communicate(self, *, input, timeout):
            assert input == (TOKEN + "\n").encode()
            if outcome == "timeout": raise subprocess.TimeoutExpired("docker", timeout, output=TOKEN)
            if outcome == "term": signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        def poll(self): return self.returncode
    def popen(argv, **kwargs):
        assert TOKEN not in repr(argv) + repr(kwargs)
        if outcome == "spawn_failure": raise OSError(TOKEN)
        return Process()
    monkeypatch.setattr(bootstrap.subprocess, "Popen", popen)
    monkeypatch.setattr(bootstrap, "dispose_container", lambda name: calls.append(name) or True)
    prior = signal.getsignal(signal.SIGTERM)
    assert bootstrap.run_worker(bootstrap.read_receipt(receipt), REPO, NAME, TOKEN.encode(), timeout=60) == code
    assert calls == [NAME]
    assert signal.getsignal(signal.SIGTERM) == prior
    assert TOKEN not in str(capsys.readouterr())


def test_bootstrap_cleanup_failure_has_fatal_distinct_exit(receipt, monkeypatch):
    proc = SimpleNamespace(returncode=0, communicate=lambda **kw: None, poll=lambda: 0)
    monkeypatch.setattr(bootstrap.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(bootstrap, "dispose_container", lambda name: False)
    assert bootstrap.run_worker(bootstrap.read_receipt(receipt), REPO, NAME, TOKEN.encode(), timeout=60) == 3


def test_old_persistent_receipt_and_option_are_rejected(receipt):
    data = json.loads(receipt.read_text()); data.pop("runner_mode")
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="invalid_ephemeral_runner_receipt"):
        bootstrap.read_receipt(receipt)
    with pytest.raises(ValueError, match="persistent_worker_retired"):
        bootstrap.container_command("sha256:" + "a" * 64, REPO, NAME, persistent=True)


def test_image_mode_must_match_even_when_image_id_matches(receipt, monkeypatch):
    value = bootstrap.read_receipt(receipt)
    image = {"Id": value["image_id"], "Config": {"Labels": {
        "diwan.runner-version": value["runner_version"], "diwan.lock-sha256": value["lock_sha256"]}}}
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps([image])))
    with pytest.raises(ValueError, match="runner_image_receipt_mismatch"):
        bootstrap.inspect_image(value)
    image["Config"]["Labels"]["diwan.runner-mode"] = "ephemeral-only-v1"
    bootstrap.inspect_image(value)


def entry_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(entry.os, "getuid", lambda: 1000)
    monkeypatch.setattr(entry, "Path", lambda value: tmp_path / str(value).lstrip("/"))
    monkeypatch.setattr(entry.shutil, "copytree", lambda *a, **kw: None)
    monkeypatch.setattr(entry.shutil, "rmtree", lambda *a, **kw: None)
    monkeypatch.setattr(entry.sys, "stdin", SimpleNamespace(buffer=io.BytesIO((TOKEN + "\n").encode())))
    monkeypatch.setattr(entry.sys, "argv", ["runner_entrypoint.py", REPO, NAME])


def test_entrypoint_forces_ephemeral_and_removes_registration_token_before_job(tmp_path, monkeypatch):
    entry_environment(tmp_path, monkeypatch)
    calls = []
    def run(argv, **kwargs):
        calls.append((list(argv), dict(kwargs["env"]), kwargs))
        assert TOKEN not in repr(argv)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(entry.subprocess, "run", run)
    assert entry.main() == 0
    assert len(calls) == 2
    assert "--ephemeral" in calls[0][0]
    assert calls[0][1]["ACTIONS_RUNNER_INPUT_TOKEN"] == TOKEN
    assert calls[0][2]["stdout"] == calls[0][2]["stderr"] == subprocess.DEVNULL
    assert calls[1][0] == ["./run.sh"]
    assert "ACTIONS_RUNNER_INPUT_TOKEN" not in calls[1][1]
    assert calls[0][2]["env"] == {}  # Original temporary environment was cleared.


def test_entrypoint_registration_timeout_never_starts_worker_or_prints_token(tmp_path, monkeypatch, capsys):
    entry_environment(tmp_path, monkeypatch)
    calls = []
    def fail(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, 120, output=TOKEN, stderr=TOKEN)
    monkeypatch.setattr(entry.subprocess, "run", fail)
    assert entry.main() == 1
    assert len(calls) == 1
    assert TOKEN not in str(capsys.readouterr())


@pytest.mark.parametrize("problem", ["persistent", "reused"])
def test_entrypoint_rejects_persistence_or_existing_worker_state(tmp_path, monkeypatch, problem):
    entry_environment(tmp_path, monkeypatch)
    monkeypatch.setattr(entry.subprocess, "run", lambda *a, **k: pytest.fail("must refuse before registration"))
    if problem == "persistent":
        monkeypatch.setattr(sys, "argv", ["runner_entrypoint.py", REPO, NAME, "--persistent"])
    else:
        (tmp_path / "runner").mkdir()
        (tmp_path / "runner/.runner").write_text("previous worker")
    with pytest.raises(SystemExit):
        entry.main()


def test_dry_run_has_no_api_docker_or_state_mutation(receipt, tmp_path, monkeypatch, capsys):
    state = tmp_path / "not-created"
    monkeypatch.setattr(sys, "argv", ["run_persistent_runner.py", "--dry-run", "--runner-receipt", str(receipt), "--state-dir", str(state)])
    monkeypatch.setattr(supervisor.shutil, "which", lambda tool: "/trusted/" + tool)
    monkeypatch.setattr(supervisor.subprocess, "run", lambda *a, **k: pytest.fail("dry run invoked subprocess"))
    assert supervisor.main() == 0
    assert not state.exists()
    assert "unverified" in capsys.readouterr().out


def test_clean_worker_expiry_rotates_without_exhausting_idle_failure_budget(receipt, tmp_path, monkeypatch):
    tokens = []
    monkeypatch.setattr(supervisor, "get_registration_token", lambda repo: tokens.append(repo) or TOKEN)
    monkeypatch.setattr(supervisor, "run_one_worker", lambda *a, **k: 124)
    assert supervisor.run_persistent_runner(receipt, REPO, state_dir=tmp_path / "private",
        max_workers=2, max_failures=1, stop=Stop()) == 0
    assert len(tokens) == 2


def test_controlled_stop_is_not_a_registration_failure(receipt, tmp_path, monkeypatch):
    stop = Stop()
    monkeypatch.setattr(supervisor, "get_registration_token", lambda repo: TOKEN)
    def worker(*args, **kwargs):
        stop.set()
        return 143
    monkeypatch.setattr(supervisor, "run_one_worker", worker)
    assert supervisor.run_persistent_runner(receipt, REPO, state_dir=tmp_path / "private",
        max_failures=1, stop=stop) == 0


def test_dry_run_rejects_invalid_bounds_before_api_or_docker(receipt, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_persistent_runner.py", "--dry-run", "--runner-receipt", str(receipt), "--timeout", "0"])
    monkeypatch.setattr(supervisor.subprocess, "run", lambda *a, **k: pytest.fail("must reject locally"))
    with pytest.raises(SystemExit) as exc:
        supervisor.main()
    assert exc.value.code == 2


def test_late_container_creation_during_client_termination_precedes_final_cleanup(receipt, monkeypatch):
    events = []
    present = False
    class Process:
        returncode = None
        def communicate(self, **kwargs):
            raise subprocess.TimeoutExpired("docker", 60)
        def poll(self): return self.returncode
        def terminate(self):
            nonlocal present
            present = True  # A delayed docker-run request becomes a container now.
            events.append("late_create")
        def wait(self, **kwargs):
            self.returncode = -15
            events.append("creator_reaped")
    def dispose(name):
        nonlocal present
        events.append("final_disposal")
        present = False
        return True
    monkeypatch.setattr(bootstrap.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(bootstrap, "dispose_container", dispose)
    assert bootstrap.run_worker(bootstrap.read_receipt(receipt), REPO, NAME, TOKEN.encode(), timeout=60) == 124
    assert events == ["late_create", "creator_reaped", "final_disposal"]
    assert present is False


def test_unreaped_creator_never_claims_verified_cleanup(receipt, monkeypatch):
    class Process:
        returncode = None
        def communicate(self, **kwargs): raise subprocess.TimeoutExpired("docker", 60)
        def poll(self): return None
        def terminate(self): pass
        def kill(self): pass
        def wait(self, **kwargs): raise subprocess.TimeoutExpired("docker", 10)
    monkeypatch.setattr(bootstrap.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(bootstrap, "dispose_container", lambda name: True)
    assert bootstrap.run_worker(bootstrap.read_receipt(receipt), REPO, NAME, TOKEN.encode(), timeout=60) == 3


def test_bootstrap_receipt_substitution_refuses_before_stdin_or_docker(receipt, monkeypatch, capsys):
    fingerprint = hashlib.sha256(receipt.read_bytes()).hexdigest()
    changed = json.loads(receipt.read_text()); changed["image_id"] = "sha256:" + "c" * 64
    receipt.write_text(json.dumps(changed))
    monkeypatch.setattr(sys, "argv", ["start_isolated_runner.py", "--runner-receipt", str(receipt),
        "--repository", REPO, "--expected-receipt-sha256", fingerprint])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: pytest.fail("must not inspect or read stdin")))
    monkeypatch.setattr(bootstrap, "inspect_image", lambda receipt: pytest.fail("must reject before Docker inspect"))
    with pytest.raises(SystemExit) as exc:
        bootstrap.main()
    assert exc.value.code == 2
    assert "runner_preflight_failed" in capsys.readouterr().err


def test_supervisor_checkpoint_and_child_share_original_receipt_fingerprint(receipt, tmp_path, monkeypatch):
    fingerprint = hashlib.sha256(receipt.read_bytes()).hexdigest()
    state = tmp_path / "private"
    def registration(repo):
        changed = json.loads(receipt.read_text()); changed["image_id"] = "sha256:" + "c" * 64
        receipt.write_text(json.dumps(changed))  # Owner update between initial pin and checkpoint.
        return TOKEN
    def worker(receipt_path, repo, token, name, **kwargs):
        checkpoint = json.loads(next(state.glob("*.active.json")).read_text())
        assert checkpoint["receipt_sha256"] == kwargs["receipt_sha256"] == fingerprint
        assert hashlib.sha256(receipt_path.read_bytes()).hexdigest() != fingerprint
        with pytest.raises(ValueError, match="runner_receipt_changed"):
            bootstrap.read_receipt(receipt_path, kwargs["receipt_sha256"])
        return 3
    monkeypatch.setattr(supervisor, "get_registration_token", registration)
    monkeypatch.setattr(supervisor, "run_one_worker", worker)
    assert supervisor.run_persistent_runner(receipt, REPO, state_dir=state, stop=Stop()) == 3
    assert next(state.glob("*.active.json")).exists()


def test_bootstrap_uses_loaded_snapshot_if_receipt_changes_after_verification(receipt, monkeypatch):
    original = json.loads(receipt.read_text())
    fingerprint = hashlib.sha256(receipt.read_bytes()).hexdigest()
    monkeypatch.setattr(sys, "argv", ["start_isolated_runner.py", "--runner-receipt", str(receipt),
        "--repository", REPO, "--name", NAME, "--expected-receipt-sha256", fingerprint])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: False, buffer=io.BytesIO((TOKEN + "\n").encode())))
    def inspect(snapshot):
        assert snapshot == original
        receipt.write_text(json.dumps({**original, "image_id": "sha256:" + "c" * 64}))
    def worker(snapshot, *args, **kwargs):
        assert snapshot == original
        return 0
    monkeypatch.setattr(bootstrap, "inspect_image", inspect)
    monkeypatch.setattr(bootstrap, "run_worker", worker)
    assert bootstrap.main() == 0


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_receipt_leaf_aliases_are_refused(receipt, tmp_path, alias):
    path = tmp_path / "aliased-receipt.json"
    if alias == "symlink": path.symlink_to(receipt)
    else: os.link(receipt, path)
    with pytest.raises((OSError, ValueError)):
        bootstrap.read_receipt(path)


def test_state_ancestor_symlink_refuses_before_registration(receipt, tmp_path, monkeypatch):
    real = tmp_path / "real"; real.mkdir()
    link = tmp_path / "link"; link.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(supervisor, "get_registration_token", lambda *a: pytest.fail("aliased state registered worker"))
    with pytest.raises(supervisor.SupervisorRefused, match="supervisor_state_path_invalid"):
        supervisor.run_persistent_runner(receipt, REPO, state_dir=link / "private", stop=Stop())
    assert not (real / "private").exists()


def test_state_directory_replacement_never_deletes_another_checkpoint(tmp_path):
    state = tmp_path / "private"
    with supervisor.supervisor_lock(state, REPO) as active:
        supervisor._begin_worker(active, REPO, NAME, "a" * 64)
        moved = tmp_path / "old-state"; state.rename(moved)
        state.mkdir(mode=0o700)
        other = state / active.name
        other.write_text("replacement checkpoint")
        with pytest.raises(supervisor.SupervisorRefused, match="supervisor_state_path_changed"):
            supervisor._finish_worker(active)
        assert other.read_text() == "replacement checkpoint"
        assert (moved / active.name).exists()


def test_checkpoint_replacement_is_not_deleted_by_original_worker(tmp_path):
    state = tmp_path / "private"
    with supervisor.supervisor_lock(state, REPO) as active:
        supervisor._begin_worker(active, REPO, NAME, "a" * 64)
        replacement = state / "replacement"
        replacement.write_text("a newer checkpoint")
        os.replace(replacement, state / active.name)
        with pytest.raises(supervisor.SupervisorRefused, match="active_worker_checkpoint_replaced"):
            supervisor._finish_worker(active)
        assert (state / active.name).read_text() == "a newer checkpoint"


def test_state_swapped_during_registration_refuses_before_worker_start(receipt, tmp_path, monkeypatch):
    state = tmp_path / "private"
    def registration(repo):
        state.rename(tmp_path / "old-state")
        state.mkdir(mode=0o700)
        return TOKEN
    monkeypatch.setattr(supervisor, "get_registration_token", registration)
    monkeypatch.setattr(supervisor, "run_one_worker", lambda *a, **k: pytest.fail("state changed but worker launched"))
    with pytest.raises(supervisor.SupervisorRefused, match="supervisor_state_path_changed"):
        supervisor.run_persistent_runner(receipt, REPO, state_dir=state, stop=Stop())
    assert not list(state.glob("*.active.json"))


def test_replaced_lock_is_detected_before_checkpoint_creation(tmp_path):
    state = tmp_path / "private"
    with supervisor.supervisor_lock(state, REPO) as active:
        replacement = state / "replacement"
        replacement.write_text("new lock")
        os.replace(replacement, state / active.lock_name)
        with pytest.raises(supervisor.SupervisorRefused, match="supervisor_lock_replaced"):
            supervisor._begin_worker(active, REPO, NAME, "a" * 64)
        assert not (state / active.name).exists()


@pytest.mark.parametrize("canonical,alias", [("Private", "private"), ("café", "cafe\u0301")])
def test_native_state_spelling_alias_refuses_before_registration(receipt, tmp_path, monkeypatch, canonical, alias):
    real = tmp_path / canonical
    real.mkdir(mode=0o700)
    actual = next(name for name in os.listdir(tmp_path) if (tmp_path / name).samefile(real))
    alternate = alias if actual != alias else canonical
    aliased = tmp_path / alternate
    if not aliased.exists() or not aliased.samefile(real):
        pytest.skip("filesystem does not alias these directory spellings")
    monkeypatch.setattr(supervisor, "get_registration_token", lambda *a: pytest.fail("aliased state registered worker"))
    with pytest.raises(supervisor.SupervisorRefused, match="supervisor_state_path_aliased"):
        supervisor.run_persistent_runner(receipt, REPO, state_dir=aliased, stop=Stop())
    assert not list(real.iterdir())
