"""Guards for the supported-Python hosted matrix required by issue #187."""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOSTED = (ROOT / ".github" / "workflows" / "verify-hosted.yml").read_text(encoding="utf-8")
CONTAINER = (ROOT / ".github" / "workflows" / "container-smoke.yml").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_verify_hosted_covers_every_supported_python_version():
    matrix = re.search(r"python-version:\s*\[([^]]+)]", HOSTED)
    assert matrix, "verify-hosted must declare an explicit Python matrix"
    versions = re.findall(r"['\"](\d+\.\d+)['\"]", matrix.group(1))
    assert versions == ["3.11", "3.12", "3.14"]
    assert "fail-fast: false" in HOSTED
    assert HOSTED.count('"${{ matrix.python-version }}"') >= 2


def test_hosted_actions_remain_digest_pinned_and_read_only():
    actions = re.findall(r"^\s*- uses:\s*([^\s#]+)", HOSTED, re.MULTILINE)
    assert actions
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in actions)
    assert "permissions:\n  contents: read\n" in HOSTED
    assert ": write" not in HOSTED and "secrets." not in HOSTED
    assert "persist-credentials: false" in HOSTED


def test_container_smoke_runs_pytest_inside_the_python_312_image():
    assert re.search(r"^FROM python:3\.12(?:[.-]|$)", DOCKERFILE, re.MULTILINE)
    assert "name: Python 3.12 tests in the image" in CONTAINER
    assert "diwan:ci-test python -m pytest -p no:cacheprovider" in CONTAINER
    assert "continue-on-error" not in CONTAINER


def test_container_checkout_exposes_full_history_read_only():
    checkout = re.search(
        r"- uses: actions/checkout@([0-9a-f]{40}) # v4\n"
        r"\s+with:\n"
        r"\s+persist-credentials: false\n"
        r"\s+fetch-depth: 0",
        CONTAINER,
    )
    assert checkout, "history-dependent tests require a complete read-only checkout"
    assert "permissions:\n  contents: read\n" in CONTAINER
    assert ": write" not in CONTAINER and "secrets." not in CONTAINER
