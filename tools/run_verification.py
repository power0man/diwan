#!/usr/bin/env python3
"""Run the shared checks in an already isolated, prepared runtime. No pip.

A missing/legacy signature is a failure, never a successful degraded check.
This program is not a sandbox; launch it only inside the reviewed CI container.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.public_export import read_marker
from verification_checks import commands


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="/usr/local/bin/node")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    # Defense in depth only; Docker isolation is what excludes host credentials.
    for name in tuple(env):
        if name in {"DIWAN_ANCHOR_KEY", "DIWAN_ED25519_PRIVATE_KEY",
                    "DIWAN_ED25519_PRIVATE_KEY_PATH"}:
            env.pop(name)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Only an explicitly declared check contract can interpret an exit as
    # public-export unavailability. Pytest's exit 3 is an internal error.
    unavailable_declared = read_marker(root) is not None
    for check in commands(sys.executable, args.node):
        print(f"[verify] {check.name}", flush=True)
        started = time.monotonic()
        try:
            result = subprocess.run(check.argv, cwd=root, env=env, timeout=check.timeout_s)
        except subprocess.TimeoutExpired:
            print(f"[verify] {check.name}: status=timeout limit_s={check.timeout_s} "
                  f"elapsed_s={time.monotonic() - started:.3f}", file=sys.stderr, flush=True)
            return 1
        except FileNotFoundError:
            print(f"[verify] {check.name}: status=executable_missing executable={check.argv[0]!r} "
                  f"elapsed_s={time.monotonic() - started:.3f}", file=sys.stderr, flush=True)
            return 1
        except OSError as exc:
            print(f"[verify] {check.name}: status=execution_error errno={exc.errno} "
                  f"elapsed_s={time.monotonic() - started:.3f}", file=sys.stderr, flush=True)
            return 1
        elapsed = time.monotonic() - started
        if result.returncode == check.public_unavailable_exit_code and unavailable_declared:
            print(f"[verify] {check.name}: unavailable by declared limit (public export); "
                  f"status=unavailable returncode={result.returncode} elapsed_s={elapsed:.3f}", flush=True)
            continue
        if result.returncode:
            print(f"[verify] {check.name}: failed ({result.returncode}); status=failed "
                  f"returncode={result.returncode} elapsed_s={elapsed:.3f}", file=sys.stderr, flush=True)
            return 1
        print(f"[verify] {check.name}: status=passed returncode=0 elapsed_s={elapsed:.3f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
