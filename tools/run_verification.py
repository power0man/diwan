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
    # في اللقطة العامة (ك٢٧) قد يتعذّر فحصٌ لغياب مخازن المالك؛ فالخروجُ 3 «تعذّر بحدٍّ
    # معلن» لا فشل (ق٢٥) — تحت علامة اللقطة وحدها، وفي المستودع الخاص يبقى الصارم.
    unavailable_declared = read_marker(root) is not None
    for check in commands(sys.executable, args.node):
        print(f"[verify] {check.name}", flush=True)
        try:
            result = subprocess.run(check.argv, cwd=root, env=env, timeout=check.timeout_s)
        except (OSError, subprocess.TimeoutExpired):
            print(f"[verify] {check.name}: execution unavailable", file=sys.stderr)
            return 1
        if result.returncode == 3 and unavailable_declared:
            print(f"[verify] {check.name}: unavailable by declared limit (public export)", flush=True)
            continue
        if result.returncode:
            print(f"[verify] {check.name}: failed ({result.returncode})", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
