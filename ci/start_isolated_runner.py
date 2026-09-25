#!/usr/bin/env python3
"""Reviewed bootstrap: one token, one ephemeral worker, verified disposal.

Run only an owner-reviewed installed copy outside candidate checkouts. Registration
input is accepted only on stdin. Exit 3 means cleanup could not be proved: do not
start another worker. A supervisor may call this command repeatedly, sequentially.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import json
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import uuid

RUNNER_MODE = "ephemeral-only-v1"
CLEANUP_UNVERIFIED = 3
REPOSITORY = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
RUNNER_NAME = re.compile(r"diwan-isolated-[a-f0-9]{16}\Z")
LABELS = frozenset({"diwan-isolated", "diwan-isolated-canary"})


def receipt_snapshot(path: Path, expected_sha256: str | None = None) -> tuple[dict, str]:
    """Read once, pin those exact bytes, and parse only that immutable snapshot."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("runner_receipt_not_independent_regular_file")
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("runner_receipt_too_large")
    fingerprint = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and fingerprint != expected_sha256:
        raise ValueError("runner_receipt_changed")
    receipt = json.loads(raw)
    if (not isinstance(receipt, dict) or receipt.get("schema_version") != 1
            or receipt.get("runner_version") != "2.337.0"
            or receipt.get("runner_mode") != RUNNER_MODE
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", str(receipt.get("image_id", "")))
            or not re.fullmatch(r"[a-f0-9]{64}", str(receipt.get("lock_sha256", "")))):
        raise ValueError("invalid_ephemeral_runner_receipt")
    return receipt, fingerprint


def read_receipt(path: Path, expected_sha256: str | None = None) -> dict:
    return receipt_snapshot(path, expected_sha256)[0]


def inspect_image(receipt: dict) -> None:
    inspected = subprocess.run(["docker", "image", "inspect", receipt["image_id"]],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, timeout=30)
    if inspected.returncode:
        raise ValueError("runner_image_unavailable")
    try:
        image = json.loads(inspected.stdout)[0]
        labels = image["Config"].get("Labels") or {}
        valid = (image["Id"] == receipt["image_id"]
                 and labels.get("diwan.runner-version") == receipt["runner_version"]
                 and labels.get("diwan.runner-mode") == RUNNER_MODE
                 and labels.get("diwan.lock-sha256") == receipt["lock_sha256"])
    except (ValueError, KeyError, IndexError, TypeError):
        valid = False
    if not valid:
        raise ValueError("runner_image_receipt_mismatch")


def container_command(image_id: str, url: str, name: str, persistent: bool = False,
                      *, label: str = "diwan-isolated") -> list[str]:
    if persistent:
        raise ValueError("persistent_worker_retired")
    if not RUNNER_NAME.fullmatch(name) or not REPOSITORY.fullmatch(url) or label not in LABELS:
        raise ValueError("invalid_worker_identity")
    return ["docker", "run", "--rm", "--pull=never", "--init", "--interactive", "--name", name,
            "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--read-only", "--pids-limit", "512", "--memory", "6g", "--cpus", "4",
            "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=1g,uid=1000,gid=1000,mode=700",
            "--tmpfs", "/home/runner:rw,nosuid,nodev,size=64m,uid=1000,gid=1000,mode=700",
            "--tmpfs", "/runner:rw,exec,nosuid,nodev,size=4g,uid=1000,gid=1000,mode=700",
            image_id, url, name, label]


def dispose_container(name: str) -> bool:
    """A failed rm is safe only if a successful listing proves absence."""
    if not RUNNER_NAME.fullmatch(name):
        return False
    try:
        removal = subprocess.run(["docker", "rm", "--force", name],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 timeout=20)
        if removal.returncode == 0:
            return True
        listed = subprocess.run(["docker", "ps", "--all", "--filter", f"name=^/{name}$",
                                 "--format", "{{.ID}}"], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=20)
        return listed.returncode == 0 and not listed.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False


class _Stopped(Exception):
    def __init__(self, signum):
        self.signum = signum


def run_worker(receipt: dict, repository: str, name: str, token: bytes, *,
               timeout: int, label: str = "diwan-isolated") -> int:
    process = None
    status = 1
    previous = {}
    def interrupted(signum, frame):
        raise _Stopped(signum)
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, interrupted)
        try:
            process = subprocess.Popen(container_command(receipt["image_id"], repository, name, label=label),
                                       stdin=subprocess.PIPE)
            process.communicate(input=token + b"\n", timeout=timeout)
            status = 0 if process.returncode == 0 else 1
        except subprocess.TimeoutExpired:
            status = 124
            print("worker_deadline_reached: disposing container", file=sys.stderr)
        except _Stopped as exc:
            status = 128 + exc.signum
        except (OSError, KeyboardInterrupt):
            # Never include exception text: subprocess exceptions can carry output.
            print("worker_process_failed: disposing container", file=sys.stderr)
    finally:
        token = b""
        # Repeated termination signals must not interrupt the cleanup itself.
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        creator_stopped = True
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            creator_stopped = False
        try:
            # A live docker-run client can create the named container late. Its
            # exit must precede the final disposal/absence proof, even in one-shot use.
            disposed = dispose_container(name)
            if not creator_stopped or not disposed:
                print("container_cleanup_unverified: stop before another worker", file=sys.stderr)
                status = CLEANUP_UNVERIFIED
        finally:
            for sig, old in previous.items():
                signal.signal(sig, old)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-receipt", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--expected-receipt-sha256", help="supervisor pin for the exact approved receipt bytes")
    parser.add_argument("--timeout", type=int, default=3600, help="total seconds including queue wait")
    parser.add_argument("--name", default=None, help="unique supervisor-owned worker identity")
    parser.add_argument("--label", choices=sorted(LABELS), default="diwan-isolated")
    args = parser.parse_args()
    if not REPOSITORY.fullmatch(args.repository):
        parser.error("invalid_repository")
    if not 60 <= args.timeout <= 86400:
        parser.error("timeout must be between 60 and 86400 seconds")
    name = args.name or "diwan-isolated-" + uuid.uuid4().hex[:16]
    if not RUNNER_NAME.fullmatch(name):
        parser.error("invalid_worker_name")
    try:
        receipt = read_receipt(args.runner_receipt, args.expected_receipt_sha256)
        inspect_image(receipt)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        parser.error("runner_preflight_failed: review receipt and image")
    if sys.stdin.isatty():
        parser.error("registration input requires a pipe")
    token = sys.stdin.buffer.readline(4097).strip()
    if not token or len(token) > 4096 or not re.fullmatch(rb"[A-Za-z0-9_\-.]+", token):
        parser.error("invalid_registration_input")
    return run_worker(receipt, args.repository, name, token, timeout=args.timeout, label=args.label)


if __name__ == "__main__":
    raise SystemExit(main())
