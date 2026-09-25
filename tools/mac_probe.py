#!/usr/bin/env python3
"""Local Mac inventory with a closed public schema and no raw command output.

This does not list models, MCP servers, credentials, command stderr, user paths,
external configuration, or generate text. Run manually in the owner's trusted
checkout; this tool is not dispatched or auto-published by a workflow.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _value(cmd: list[str]) -> str | None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _integer(cmd: list[str], maximum: int) -> int | None:
    value = _value(cmd)
    if value is None or not re.fullmatch(r"[0-9]{1,16}", value):
        return None
    number = int(value)
    return number if 0 < number <= maximum else None


def _version(cmd: list[str], prefix: str = "") -> str | None:
    value = _value(cmd)
    if value is None:
        return None
    match = re.fullmatch(re.escape(prefix) + r"([0-9]{1,3}(?:\.[0-9]{1,3}){1,3})", value)
    return match.group(1) if match else None


def collect() -> dict:
    mem = _integer(["/usr/sbin/sysctl", "-n", "hw.memsize"], 2**50)
    cores = _integer(["/usr/sbin/sysctl", "-n", "hw.ncpu"], 65536)
    ram_gb = round(mem / 2**30) if mem is not None else None
    machine = platform.machine()
    if machine not in {"arm64", "aarch64", "x86_64", "i386"}:
        machine = None
    try:
        disk = shutil.disk_usage("/")
        storage = {"total_bytes": disk.total, "free_bytes": disk.free}
    except OSError:
        storage = None
    probe = {
        "schema_version": 2,
        "probed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": {
            "machine": machine,
            "macos_version": _version(["/usr/bin/sw_vers", "-productVersion"]),
            "cpu_cores": cores,
            "ram_gb": ram_gb,
            "storage": storage,
        },
        "tooling": {
            "python_version": ".".join(str(v) for v in sys.version_info[:3]),
            "git_version": _version(["/usr/bin/git", "--version"], "git version "),
            "ollama_present": shutil.which("ollama") is not None,
            "hermes_present": shutil.which("hermes") is not None,
        },
    }
    if ram_gb:
        usable = max(0, ram_gb - 8)
        probe["capacity_estimate"] = {
            "note_ar": "تقريب لا وعد تشغيل؛ الاحتياطي ٨ غ.ب والسياق يحتاج قياسًا",
            "usable_gb": usable,
            "max_params_b_at_4bit": usable * 2,
            "max_params_b_at_8bit": usable,
        }
    return probe


def main(out_dir: str) -> int:
    probe = collect()
    out = Path(out_dir)
    stamp = time.strftime("%Y-%m-%d-%H%M%S", time.gmtime())
    path = out / f"mac-{stamp}.json"
    try:
        out.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(probe, stream, ensure_ascii=False, indent=1, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        print('{"status":"refused","code":"probe_output_unavailable"}')
        return 1
    print(json.dumps({"status": "written", "schema_version": 2,
                      "ram_measured": probe["host"]["ram_gb"] is not None}))
    return 0 if probe["host"]["ram_gb"] is not None else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "var/probe"))
