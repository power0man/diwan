"""OpenCode V2 bridge with an explicit provider/model, never an implicit model family.

Uses native approvals without --auto. No process sandbox is added. A task can stop
at an approval request; installation and JSON parsing are not live acceptance.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

from ._opencode import text_digest

from team.catalog import cli_path, model_family
from .base import Adapter, AgentSpec, ReviewResult, WorkerResult, parse_verdict, unavailable_code, assert_no_bypass, worker_env


class ModelUnconfigured(RuntimeError):
    code = "opencode_model_unconfigured"


class OpenCodeAdapter(Adapter):
    def __init__(self, binary: Path | None = None, model: str | None = None, agent: str | None = None):
        self.binary = Path(binary) if binary else cli_path("opencode") or Path("/nonexistent/opencode")
        self.model = model if model is not None else os.environ.get("DIWAN_TEAM_OPENCODE_MODEL", "")
        self.agent = agent if agent is not None else os.environ.get("DIWAN_TEAM_OPENCODE_AGENT", "")
        family = model_family(self.model)
        self.spec = AgentSpec(name="opencode", family=family,
                              capabilities={"coding": False, "planning": True, "review": True},
                              isolation={"level": "none", "mechanism": "native_tool_permissions_only"},
                              execution={"interactive": False, "structured_output": "jsonl"},
                              availability={"type": "provider_configured", "dynamic": True},
                              cost={"accounting": "estimate"}, trust={"reviewer_eligible": False})

    def _argv(self, agent: str) -> list[str]:
        if "/" not in self.model or self.spec.family in ("unknown", "moonshot"):
            raise ModelUnconfigured()
        if self.agent and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.agent):
            raise ModelUnconfigured()
        return [sys.executable, str(Path(__file__).with_name("_opencode.py")), "--binary", str(self.binary),
                "--model", self.model, "--agent", self.agent or agent]

    def work_argv(self, worktree: Path, budget_usd: float, out_dir: Path) -> list[str]:
        return self._argv("build")

    def review_argv(self, worktree: Path, base_branch: str) -> list[str]:
        return self._argv("plan")

    def run_review(self, argv: list[str], stdin_text: str, cwd: Path, timeout: int) -> tuple[int, str, str]:
        """Terminate the bridge and its private CLI server together on timeout."""
        assert_no_bypass(argv)
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=str(cwd), env=worker_env(), text=True, start_new_session=True)
        except FileNotFoundError:
            return 127, "", "binary_missing"
        try:
            out, err = proc.communicate(stdin_text, timeout=timeout)
            return proc.returncode, out, err
        except subprocess.TimeoutExpired:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    pass
                try:
                    proc.communicate(timeout=6)
                    break
                except subprocess.TimeoutExpired:
                    continue
            return 124, "", "timeout"

    def parse_work(self, returncode: int, stdout: str, stderr: str, out_dir: Path) -> WorkerResult:
        text, session, finished, failed = [], None, False, False
        proof = None
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            event_session = event.get("sessionID")
            if event_session and session and event_session != session:
                failed = True
            session = event_session or session
            if proof is not None:
                failed = True
            if event.get("type") == "diwan_session_complete":
                proof = event
                continue
            part = event.get("part") or {}
            if not isinstance(part, dict):
                failed = True
                continue
            if event.get("type") == "text" and isinstance(part.get("text"), str):
                text.append(part["text"])
            if event.get("type") == "step_finish":
                finished = part.get("reason") == "stop"
                failed = failed or part.get("reason") in ("length", "error")
            if event.get("type") == "error":
                failed = True
        if proof is not None:
            finished = (proof.get("source") == "opencode.session.export" and proof.get("model") == self.model
                        and proof.get("finish") == "stop" and proof.get("sessionID") == session
                        and proof.get("text_sha256") == text_digest("".join(text)))
        unavailable = unavailable_code(returncode, stdout + "\n" + stderr) if returncode or failed else None
        return WorkerResult(ok=returncode == 0 and finished and bool(text) and not failed and unavailable is None,
                            text="\n".join(text), session_id=session, returncode=returncode, unavailable=unavailable)

    def parse_review(self, returncode: int, stdout: str, stderr: str) -> ReviewResult:
        result = self.parse_work(returncode, stdout, stderr, Path("."))
        return ReviewResult(ok=result.ok, verdict=parse_verdict(result.text), text=result.text,
                            returncode=returncode, unavailable=result.unavailable)
