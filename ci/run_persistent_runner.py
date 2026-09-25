#!/usr/bin/env python3
"""مشرف CI مستمر، وعامل عابر جديد لكل وظيفة؛ الاسم القديم للتوافق فقط.

شغّل النسخة المثبتة المراجعة خارج checkout. الدورات متسلسلة تحت قفل،
والرمز الجديد لكل دورة يعبر stdin وحده. شاهد دورة لم تُغلق يمنع إعادة
التشغيل التلقائي حتى يتحقق المالك من التنظيف. لا إعادة لعمل GitHub نفسه.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid

try:
    from .start_isolated_runner import CLEANUP_UNVERIFIED, LABELS, REPOSITORY, dispose_container, read_receipt, receipt_snapshot
except ImportError:  # Executing the installed script directly.
    from start_isolated_runner import CLEANUP_UNVERIFIED, LABELS, REPOSITORY, dispose_container, read_receipt, receipt_snapshot

DEFAULT_RECEIPT = Path.home() / "diwan-work/diwan-ci-trust/20260921-ed25519/runner.json"
DEFAULT_REPO = "https://github.com/power0man/diwan-private"  # المشغّلُ المعزول يخصّ الخاص (ق٥٢)


class SupervisorRefused(RuntimeError):
    pass


def get_registration_token(repo: str) -> str:
    """Return only a validated short-lived token; never surface API output/errors."""
    if not REPOSITORY.fullmatch(repo):
        raise SupervisorRefused("invalid_repository")
    if shutil.which("gh") is None:
        raise SupervisorRefused("registration_client_missing")
    owner_repo = repo.removeprefix("https://github.com/")
    try:
        proc = subprocess.run(
            ["gh", "api", "--method", "POST", f"repos/{owner_repo}/actions/runners/registration-token"],
            capture_output=True, text=True, check=False, timeout=30)
        if proc.returncode or len(proc.stdout) > 65536:
            raise ValueError()
        data = json.loads(proc.stdout)
        token = data.get("token", "")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_\-.]{1,4096}", token):
            raise ValueError()
        return token
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
        raise SupervisorRefused("registration_request_failed") from None


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_state_directory(path: Path, *, create: bool = False) -> int:
    """Walk exact directory entries via descriptors; no symlink or spelling aliases."""
    path = Path(path)
    if ".." in path.parts:
        raise SupervisorRefused("supervisor_state_path_invalid")
    path = path.absolute()
    fd = os.open(path.anchor, _DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            if part not in os.listdir(fd):
                try:
                    os.stat(part, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    if not create:
                        raise SupervisorRefused("supervisor_state_path_changed") from None
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                else:
                    raise SupervisorRefused("supervisor_state_path_aliased")
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except OSError:
        os.close(fd)
        raise SupervisorRefused("supervisor_state_path_invalid") from None
    except BaseException:
        os.close(fd)
        raise


def _identity(info):
    return info.st_dev, info.st_ino


@dataclass
class ActiveWorkerState:
    path: Path
    directory_fd: int
    lock_fd: int
    lock_name: str
    name: str
    active_identity: tuple | None = None

    def check_identity(self) -> None:
        current = _open_state_directory(self.path)
        try:
            if _identity(os.fstat(current)) != _identity(os.fstat(self.directory_fd)):
                raise SupervisorRefused("supervisor_state_path_changed")
            try:
                lock = os.stat(self.lock_name, dir_fd=self.directory_fd, follow_symlinks=False)
            except OSError:
                raise SupervisorRefused("supervisor_lock_replaced") from None
            if _identity(lock) != _identity(os.fstat(self.lock_fd)):
                raise SupervisorRefused("supervisor_lock_replaced")
        finally:
            os.close(current)


@contextmanager
def supervisor_lock(state_dir: Path, repo: str):
    """One private directory descriptor holds the lock and all checkpoint operations."""
    state_dir = Path(state_dir).absolute()
    directory_fd = _open_state_directory(state_dir, create=True)
    fd = None
    try:
        info = os.fstat(directory_fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SupervisorRefused("supervisor_state_not_private")
        key = hashlib.sha256(repo.casefold().encode()).hexdigest()
        lock_name = key + ".lock"
        fd = os.open(lock_name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SupervisorRefused("supervisor_lock_invalid")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SupervisorRefused("supervisor_already_running") from None
        active = ActiveWorkerState(state_dir, directory_fd, fd, lock_name, key + ".active.json")
        active.check_identity()
        try:
            os.stat(active.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise SupervisorRefused("previous_worker_cleanup_required")
        yield active
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory_fd)


def _begin_worker(active: ActiveWorkerState, repo: str, name: str, receipt_sha256: str) -> None:
    active.check_identity()
    record = {"schema_version": 1, "repository": repo, "worker_name": name,
              "receipt_sha256": receipt_sha256}
    fd = os.open(active.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=active.directory_fd)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        active.active_identity = _identity(os.fstat(stream.fileno()))
        json.dump(record, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(active.directory_fd)
    active.check_identity()


def _finish_worker(active: ActiveWorkerState) -> None:
    active.check_identity()
    info = os.stat(active.name, dir_fd=active.directory_fd, follow_symlinks=False)
    if _identity(info) != active.active_identity or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SupervisorRefused("active_worker_checkpoint_replaced")
    os.unlink(active.name, dir_fd=active.directory_fd)
    os.fsync(active.directory_fd)
    active.active_identity = None


def run_one_worker(receipt: Path, repo: str, token: str, name: str, *, timeout: int,
                   receipt_sha256: str, label: str, stop: threading.Event) -> int:
    """Wait for the trusted bootstrap's disposal verdict before returning."""
    script = Path(__file__).resolve().parent / "start_isolated_runner.py"
    command = [sys.executable, str(script), "--runner-receipt", str(receipt),
               "--repository", repo, "--timeout", str(timeout), "--name", name,
               "--label", label, "--expected-receipt-sha256", receipt_sha256]
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
    except OSError:
        return CLEANUP_UNVERIFIED  # Keep the pending record; no guessed cleanup.
    payload = (token + "\n").encode("ascii")
    token = ""
    deadline = time.monotonic() + timeout + 90
    terminating_at = None
    try:
        while True:
            now = time.monotonic()
            if stop.is_set() and terminating_at is None:
                process.terminate()
                terminating_at = now
            if now >= deadline or (terminating_at is not None and now - terminating_at >= 70):
                # A stuck/killed bootstrap cannot attest cleanup. Never launch a replacement.
                process.kill()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                return CLEANUP_UNVERIFIED
            try:
                process.communicate(input=payload, timeout=1)
                if not dispose_container(name):
                    return CLEANUP_UNVERIFIED
                return int(process.returncode)
            except subprocess.TimeoutExpired:
                payload = None  # communicate resumes the same pipe; never send the token twice.
    except (OSError, KeyboardInterrupt):
        process.terminate()
        return CLEANUP_UNVERIFIED
    finally:
        payload = None


def validate_configuration(repo: str, timeout: int, max_failures: int, backoff: int,
                           max_backoff: int, max_workers: int, label: str) -> None:
    if (not REPOSITORY.fullmatch(repo) or label not in LABELS or not 60 <= timeout <= 86400
            or not 1 <= max_failures <= 20 or not 1 <= backoff <= max_backoff <= 300
            or not 0 <= max_workers <= 10000):
        raise SupervisorRefused("supervisor_configuration_invalid")


def run_persistent_runner(receipt: Path, repo: str, timeout: int = 7200, *,
                          state_dir: Path | None = None, max_failures: int = 5,
                          backoff: int = 5, max_backoff: int = 60,
                          max_workers: int = 0, label: str = "diwan-isolated",
                          stop: threading.Event | None = None) -> int:
    """The supervisor persists, but each registration/container is used once."""
    receipt = Path(receipt)
    _, receipt_sha256 = receipt_snapshot(receipt)  # Pin one exact image contract for this supervisor lifetime.
    validate_configuration(repo, timeout, max_failures, backoff, max_backoff, max_workers, label)
    state_dir = Path(state_dir) if state_dir is not None else receipt.parent / "supervisor"
    stop = stop if stop is not None else threading.Event()
    failures = workers = 0
    with supervisor_lock(state_dir, repo) as active:
        while not stop.is_set() and (max_workers == 0 or workers < max_workers):
            active.check_identity()
            read_receipt(receipt, receipt_sha256)
            token = ""
            try:
                token = get_registration_token(repo)
            except SupervisorRefused:
                status = 1
                print("registration_request_failed", file=sys.stderr, flush=True)
            else:
                if stop.is_set():
                    token = ""
                    break
                name = "diwan-isolated-" + uuid.uuid4().hex[:16]
                _begin_worker(active, repo, name, receipt_sha256)
                print(f"worker_start {name}", flush=True)
                status = run_one_worker(receipt, repo, token, name, timeout=timeout,
                                        receipt_sha256=receipt_sha256, label=label, stop=stop)
                token = ""
                workers += 1
                # Only these documented bootstrap outcomes affirm completed cleanup.
                if status not in (0, 1, 124, 130, 143):
                    print("worker_cleanup_unverified: retained active record; supervisor stopped", file=sys.stderr, flush=True)
                    return CLEANUP_UNVERIFIED
                _finish_worker(active)
                print(f"worker_disposed {name} status={status}", flush=True)
            if stop.is_set():
                break
            if status in (0, 124):
                # A clean lifetime expiry replaces infrastructure, never reruns a GitHub job.
                # Keep 124 in the event: this is not evidence that an in-flight job passed.
                failures = 0
                delay = 1  # Avoid a rapid registration loop even after successful exits.
            else:
                failures += 1
                if failures >= max_failures:
                    print("supervisor_failure_budget_exhausted", file=sys.stderr, flush=True)
                    return 1
                delay = min(max_backoff, backoff * (2 ** (failures - 1)))
                print(f"supervisor_backoff seconds={delay} failures={failures}", file=sys.stderr, flush=True)
            if stop.is_set() or (max_workers and workers >= max_workers):
                break
            stop.wait(delay)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--repository", default=DEFAULT_REPO)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--max-failures", type=int, default=5)
    parser.add_argument("--backoff", type=int, default=5)
    parser.add_argument("--max-backoff", type=int, default=60)
    parser.add_argument("--max-workers", type=int, default=0, help="0 continues until stopped; positive for canary")
    parser.add_argument("--label", choices=sorted(LABELS), default="diwan-isolated")
    parser.add_argument("--dry-run", action="store_true", help="local configuration only; no API or Docker calls")
    args = parser.parse_args()
    try:
        validate_configuration(args.repository, args.timeout, args.max_failures,
                               args.backoff, args.max_backoff, args.max_workers, args.label)
    except SupervisorRefused as exc:
        parser.error(str(exc))
    if args.dry_run:
        try:
            read_receipt(args.runner_receipt)
        except (OSError, ValueError):
            print("runner_receipt_invalid", file=sys.stderr)
            return 1
        if not REPOSITORY.fullmatch(args.repository) or any(shutil.which(tool) is None for tool in ("docker", "gh")):
            print("supervisor_prerequisite_missing", file=sys.stderr)
            return 1
        print("local_configuration_valid: image, authentication, registration and job execution are unverified")
        return 0
    stop = threading.Event()
    old = {sig: signal.signal(sig, lambda signum, frame: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        return run_persistent_runner(args.runner_receipt, args.repository, args.timeout,
            state_dir=args.state_dir, max_failures=args.max_failures, backoff=args.backoff,
            max_backoff=args.max_backoff, max_workers=args.max_workers, label=args.label, stop=stop)
    except SupervisorRefused as exc:
        print(str(exc), file=sys.stderr)  # Closed, internally constructed diagnostic codes only.
        return 1
    except (OSError, ValueError):
        print("supervisor_refused: check receipt and private state", file=sys.stderr)
        return 1
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
