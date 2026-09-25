#!/usr/bin/env python3
"""Trusted image entrypoint: configure an ephemeral runner once, then exit.

The registration token enters stdin and the configuration child's transient
ACTIONS_RUNNER_INPUT_TOKEN environment only. Config output is discarded; runtime
credentials exist in tmpfs until the host bootstrap disposes this container.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main() -> int:
    if os.getuid() != 1000 or len(sys.argv) not in (3, 4):
        raise SystemExit("runner requires UID 1000, repository, name, optional reviewed label")
    url, name = sys.argv[1:3]
    label = sys.argv[3] if len(sys.argv) == 4 else "diwan-isolated"
    if label not in {"diwan-isolated", "diwan-isolated-canary"}:
        raise SystemExit("invalid runner label; persistent workers are retired")
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", url):
        raise SystemExit("invalid repository URL")
    if not re.fullmatch(r"diwan-isolated-[a-f0-9]{16}", name):
        raise SystemExit("invalid ephemeral runner name")
    if any((Path("/runner") / path).exists() for path in (".runner", ".credentials", "_work")):
        raise SystemExit("runner_state_not_fresh")
    token = sys.stdin.buffer.readline(4097).strip()
    if not token or len(token) > 4096 or not re.fullmatch(rb"[A-Za-z0-9_\-.]+", token):
        raise SystemExit("invalid registration input")
    shutil.copytree("/opt/actions-runner", "/runner", dirs_exist_ok=True)
    env = {"PATH": "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin", "HOME": "/home/runner",
           "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    config_env = {**env, "ACTIONS_RUNNER_INPUT_TOKEN": token.decode("ascii")}
    token = b""
    command = ["./config.sh", "--unattended", "--url", url, "--name", name,
               "--labels", label, "--work", "_work", "--disableupdate", "--ephemeral"]
    try:
        configured = subprocess.run(command, cwd="/runner", env=config_env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        print("runner_registration_failed", file=sys.stderr)
        return 1
    finally:
        config_env.clear()
        shutil.rmtree(Path("/runner/_diag"), ignore_errors=True)
    if configured.returncode:
        print("runner_registration_failed", file=sys.stderr)
        return 1
    print("Ephemeral runner ready for exactly one job.", flush=True)
    return subprocess.run(["./run.sh"], cwd="/runner", env=env, stdin=subprocess.DEVNULL).returncode


if __name__ == "__main__":
    raise SystemExit(main())
