"""Continuation error ownership in the real app.js, using a deterministic fake DOM.

These checks cover client control flow; they do not claim browser rendering or
live-server persistence. The continuation backend has separate persistence tests.
"""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("check", [
    "unified_continuation_recovers_agent_unavailable_in_returned_text_mode",
    "unified_continuation_reports_project_navigation_errors",
    "unified_continuation_reports_session_navigation_errors",
    "unified_continuation_ignores_superseded_navigation_errors",
    "unified_continuation_preserves_newer_dialog_after_project_read",
])
def test_continuation_navigation_ownership(check):
    result = subprocess.run(
        ["node", "tests/webui_frontend.cjs", "webui/static/app.js", check],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"passed": true' in result.stdout, result.stdout
