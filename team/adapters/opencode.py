"""OpenCode V2 bridge with an explicit provider/model, never an implicit model family.

Uses native approvals without --auto. No process sandbox is added. A task can stop
at an approval request; installation and JSON parsing are not live acceptance.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from team.catalog import cli_path, model_family
from .base import Adapter, AgentSpec, ReviewResult, WorkerResult, parse_verdict, unavailable_code


class ModelUnconfigured(RuntimeError):
    code = "opencode_model_unconfigured"


class OpenCodeAdapter(Adapter):
    def __init__(self, binary: Path | None = None, model: str | None = None):
        self.binary = Path(binary) if binary else cli_path("opencode") or Path("/nonexistent/opencode")
        self.model = model if model is not None else os.environ.get("DIWAN_TEAM_OPENCODE_MODEL", "")
        family = model_family(self.model)
        self.spec = AgentSpec(name="opencode", family=family,
                              capabilities={"coding": True, "planning": True, "review": True},
                              isolation={"level": "none", "mechanism": "native_tool_permissions_only"},
                              execution={"interactive": False, "structured_output": "jsonl"},
                              availability={"type": "provider_configured", "dynamic": True},
                              cost={"accounting": "estimate"}, trust={"reviewer_eligible": False})

    def _argv(self, agent: str) -> list[str]:
        if "/" not in self.model or self.spec.family in ("unknown", "moonshot"):
            raise ModelUnconfigured()
        return [str(self.binary), "run", "--standalone", "--format", "json", "--model", self.model, "--agent", agent]

    def work_argv(self, worktree: Path, budget_usd: float, out_dir: Path) -> list[str]:
        return self._argv("build")

    def review_argv(self, worktree: Path, base_branch: str) -> list[str]:
        return self._argv("plan")

    def parse_work(self, returncode: int, stdout: str, stderr: str, out_dir: Path) -> WorkerResult:
        text, session, finished, failed = [], None, False, False
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            session = event.get("sessionID") or session
            part = event.get("part") or {}
            if not isinstance(part, dict):
                failed = True
                continue
            if event.get("type") == "text" and isinstance(part.get("text"), str):
                text.append(part["text"])
            if event.get("type") == "step_finish" and part.get("reason") == "stop":
                finished = True
            if event.get("type") == "error":
                failed = True
        unavailable = unavailable_code(returncode, stdout + "\n" + stderr) if returncode or failed else None
        return WorkerResult(ok=returncode == 0 and finished and bool(text) and not failed and unavailable is None,
                            text="\n".join(text), session_id=session, returncode=returncode, unavailable=unavailable)

    def parse_review(self, returncode: int, stdout: str, stderr: str) -> ReviewResult:
        result = self.parse_work(returncode, stdout, stderr, Path("."))
        return ReviewResult(ok=result.ok, verdict=parse_verdict(result.text), text=result.text,
                            returncode=returncode, unavailable=result.unavailable)
