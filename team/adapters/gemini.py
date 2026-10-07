"""Gemini CLI JSON worker. Project review eligibility remains in ProjectAdapter.

auto_edit grants edit permissions, not blanket shell approvals. Gemini's sandbox
must start successfully; this adapter does not claim process-level isolation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from team.catalog import cli_path
from .base import Adapter, AgentSpec, ReviewResult, WorkerResult, parse_verdict, unavailable_code

SPEC = AgentSpec(name="gemini", family="google", capabilities={"coding": True, "planning": True, "review": True},
                 isolation={"level": "partial", "mechanism": "gemini_native_sandbox"},
                 execution={"interactive": False, "structured_output": "json"},
                 availability={"type": "subscription_or_api", "dynamic": True},
                 cost={"accounting": "tokens_only"}, trust={"reviewer_eligible": False})


def parse_result(rc: int, stdout: str, stderr: str) -> WorkerResult:
    try:
        data = json.loads(stdout)
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    text = data.get("response")
    valid = rc == 0 and not data.get("error") and isinstance(text, str) and bool(text.strip())
    unavailable = unavailable_code(rc, stdout + "\n" + stderr) if not valid else None
    return WorkerResult(ok=valid and unavailable is None, text=text if isinstance(text, str) else "",
                        session_id=data.get("session_id"), returncode=rc, unavailable=unavailable)


class GeminiAdapter(Adapter):
    spec = SPEC

    def __init__(self, binary: Path | None = None, model: str | None = None):
        self.binary = Path(binary) if binary else cli_path("gemini") or Path("/nonexistent/gemini")
        self.model = model if model is not None else os.environ.get("DIWAN_TEAM_GEMINI_MODEL", "")

    def model_flags(self) -> list[str]:
        return ["--model", self.model] if self.model else []

    def work_argv(self, worktree: Path, budget_usd: float, out_dir: Path) -> list[str]:
        return [str(self.binary), *self.model_flags(), "-p", "اتبع التكليف الوارد على stdin.", "--output-format", "json",
                "--approval-mode", "auto_edit", "--sandbox"]

    def parse_work(self, returncode: int, stdout: str, stderr: str, out_dir: Path) -> WorkerResult:
        return parse_result(returncode, stdout, stderr)

    def review_argv(self, worktree: Path, base_branch: str) -> list[str]:
        return [str(self.binary), *self.model_flags(), "-p", "راجع المدخل الوارد على stdin.", "--output-format", "json",
                "--approval-mode", "plan", "--sandbox"]

    def parse_review(self, returncode: int, stdout: str, stderr: str) -> ReviewResult:
        result = parse_result(returncode, stdout, stderr)
        return ReviewResult(ok=result.ok, verdict=parse_verdict(result.text), text=result.text,
                            returncode=returncode, unavailable=result.unavailable)
