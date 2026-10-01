"""Exercise the real Worker handler with synthetic runtime identity and HTTP IO.

This proves handler behavior, not that Cloudflare authenticated a deployed
request. The deployment gate separately requires a real Access smoke test.
"""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("case", [
    "unconfigured", "no_access", "wrong_audience", "wrong_target", "query", "route",
    "cross_site", "bad_origin", "bad_csrf", "body_headers", "body_limit",
    "body_short", "body_long", "private_request", "redirect", "private_response",
    "unavailable", "streaming", "navigation",
])
def test_bridge_boundary(case):
    result = subprocess.run(
        ["node", "tests/cloudflare_hf_bridge.mjs", case],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
