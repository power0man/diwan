"""Antigravity CLI bridge; tool name does not determine the selected model family.

The installed agy help/models commands describe the flags and model IDs. Native
stream input carries one user event; only a terminal SUCCESS result is completion.
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
                 execution={"interactive": False, "structured_output": "jsonl"},
                              availability={"type": "subscription_or_api", "dynamic": True},
                              cost={"accounting": "tokens_only"}, trust={"reviewer_eligible": False})

    def _argv(self, mode: str) -> list[str]:
        if not self.model or self.spec.family not in ("google", "anthropic", "openai"):
            raise AntigravityModelUnconfigured()
        # --print takes its prompt from argv, not stdin. Native stream input preserves
        # the full prompt without exposing it in the process arguments. Disabling
        # slash expansion also disables --mode in agy 1.3.1, so retain native modes.
        return [str(self.binary), "--input-format", "stream-json", "--output-format", "stream-json",
                "--model", self.model, "--mode", mode, "--sandbox"]

    @staticmethod
    def _input(prompt: str) -> str:
        return json.dumps({"event": "user", "message": {"role": "user", "content": prompt}}, ensure_ascii=False) + "\n"

    def start(self, argv, stdin_text, cwd, stdout_path, stderr_path, exit_path=None):
        return super().start(argv, self._input(stdin_text), cwd, stdout_path, stderr_path, exit_path)

    def run_review(self, argv, stdin_text, cwd, timeout):
        return super().run_review(argv, self._input(stdin_text), cwd, timeout)

    def work_argv(self, worktree: Path, budget_usd: float, out_dir: Path) -> list[str]:
        return self._argv("accept-edits")

    def review_argv(self, worktree: Path, base_branch: str) -> list[str]:
        return self._argv("plan")

    def parse_work(self, returncode: int, stdout: str, stderr: str, out_dir: Path) -> WorkerResult:
        data, failed = {}, False
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("event") == "error":
                failed = True
            if event.get("event") == "result":
                payload = event.get("result")
                if not isinstance(payload, dict):
                    failed = True
                    continue
                data = payload
                if data.get("status") != "SUCCESS" or data.get("error"):
                    failed = True
        text = data.get("response")
        valid = (returncode == 0 and not failed and data.get("status") == "SUCCESS"
                 and isinstance(text, str) and bool(text.strip()))
        unavailable = unavailable_code(returncode, stdout + "\n" + stderr) if not valid else None
        return WorkerResult(ok=valid and unavailable is None, text=text if isinstance(text, str) else "",
                            session_id=data.get("conversation_id"), returncode=returncode, unavailable=unavailable)

    def parse_review(self, returncode: int, stdout: str, stderr: str) -> ReviewResult:
        result = self.parse_work(returncode, stdout, stderr, Path("."))
        return ReviewResult(ok=result.ok, verdict=parse_verdict(result.text), text=result.text,
                            returncode=returncode, unavailable=result.unavailable)
