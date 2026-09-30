"""حارس DOM لعزل البصمة اللاتينية عن عنوانها العربي."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def _frontend_check(name: str) -> dict:
    if NODE is None:
        pytest.skip("node_unavailable")
    run = subprocess.run(
        [NODE, "tests/webui_frontend.cjs", "webui/static/app.js", name],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(run.stdout)
    assert report["browser_rendering"] == "not_tested"
    assert report["checks"] == [{"name": name, "passed": True}]
    return report


def test_approval_hash_is_isolated_from_its_arabic_label() -> None:
    _frontend_check("approval_hash_with_numeric_prefix_is_isolated_from_its_arabic_label")


def test_forgotten_memory_receipt_hash_is_isolated_from_its_arabic_label() -> None:
    _frontend_check("forgotten_memory_receipt_with_numeric_prefix_is_isolated_from_its_arabic_label")
