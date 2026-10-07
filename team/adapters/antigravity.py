"""Antigravity CLI bridge; tool name does not determine the selected model family.

The installed agy help/models commands describe the flags and model IDs. JSON
parsing is contract-tested; native generation still needs a live smoke before pinning.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from team.catalog import cli_path, model_family
from .base import Adapter, AgentSpec, ReviewResult, WorkerResult, parse_verdict, unavailable_code
from .opencode import ModelUnconfigured


class AntigravityModelUnconfigured(ModelUnconfigured):
    code = "antigravity_model_unconfigured"


class AntigravityAdapter(Adapter):
    def __init__(self, binary: Path | None = None, model: str | None = None):
        self.binary = Path(binary) if binary else cli_path("antigravity") or Path("/nonexistent/agy")
        self.model = model if model is not None else os.environ.get("DIWAN_TEAM_ANTIGRAVITY_MODEL", "")
        self.spec = AgentSpec(name="antigravity", family=model_family(self.model),
                              capabilities={"coding": True, "planning": True, "review": True},
                              isolation={"level": "partial", "mechanism": "agy_terminal_restrictions"},
                              execution={"interactive": False, "structured_output": "json"},
                              availability={"type": "subscription_or_api", "dynamic": True},
                              cost={"accounting": "tokens_only"}, trust={"reviewer_eligible": False})

    def _argv(self, mode: str) -> list[str]:
        if not self.model or self.spec.family not in ("google", "anthropic", "openai"):
            raise AntigravityModelUnconfigured()
        return [str(self.binary), "--print", "--input-format", "text", "--output-format", "json",
                "--model", self.model, "--mode", mode, "--sandbox", "--disable-slash-commands"]

    def work_argv(self, worktree: Path, budget_usd: float, out_dir: Path) -> list[str]:
        return self._argv("accept-edits")

    def review_argv(self, worktree: Path, base_branch: str) -> list[str]:
        return self._argv("plan")

    def parse_work(self, returncode: int, stdout: str, stderr: str, out_dir: Path) -> WorkerResult:
        try:
            data = json.loads(stdout)
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        text = data.get("result")
        valid = (returncode == 0 and not data.get("is_error") and not data.get("error")
                 and isinstance(text, str) and bool(text.strip()))
        unavailable = unavailable_code(returncode, stdout + "\n" + stderr) if not valid else None
        return WorkerResult(ok=valid and unavailable is None, text=text if isinstance(text, str) else "",
                            session_id=data.get("session_id"), returncode=returncode, unavailable=unavailable)

    def parse_review(self, returncode: int, stdout: str, stderr: str) -> ReviewResult:
        result = self.parse_work(returncode, stdout, stderr, Path("."))
        return ReviewResult(ok=result.ok, verdict=parse_verdict(result.text), text=result.text,
                            returncode=returncode, unavailable=result.unavailable)
