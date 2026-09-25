"""Execute the workflow's actual range selection against a real synthetic Git history."""
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def history(tmp_path):
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-09-23T00:00:00Z",
           "GIT_COMMITTER_DATE": "2026-09-23T00:00:00Z"}
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, env=env, check=True,
                              capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry/agents.json").write_bytes((ROOT / "registry/agents.json").read_bytes())
    def commit(name, tagged):
        (tmp_path / "fixture.txt").write_text(name)
        git("add", "fixture.txt", "registry/agents.json")
        message = name + ("\n\nDiwan-Agent: openai/codex" if tagged else "")
        git("commit", "-qm", message)
        return git("rev-parse", "HEAD")
    old = commit("historical untagged fixture", False)
    base = commit("tagged baseline", True)
    untagged = commit("new untagged fixture", False)
    head = commit("tagged selected commit", True)
    return tmp_path, old, base, untagged, head


def run_actual_workflow(history, event, base, head):
    path, *_ = history
    yaml = (ROOT / ".github/workflows/verify.yml").read_text()
    section = yaml.split("      - name: Every commit names its producing agent\n", 1)[1]
    section = section.split("        run: |\n", 1)[1].split("      - name:", 1)[0]
    script = "\n".join(line[10:] for line in section.splitlines() if line.startswith("          "))
    command = shlex.join([sys.executable, str(ROOT / "tools/agent_attribution.py")])
    script = script.replace("/opt/venv/bin/python tools/agent_attribution.py", command)
    return subprocess.run(["/bin/sh", "-c", script], cwd=path,
        env={**os.environ, "EVENT_NAME": event, "RANGE_BASE": base, "RANGE_HEAD": head},
        capture_output=True, text=True)


def test_manual_rechecks_selected_commit_without_certifying_history(history):
    _, _, _, untagged, head = history
    assert run_actual_workflow(history, "workflow_dispatch", "", head).returncode == 0
    assert run_actual_workflow(history, "workflow_dispatch", "", untagged).returncode == 1


@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_incoming_ranges_still_reject_an_untagged_intermediate_commit(history, event):
    _, _, base, untagged, head = history
    result = run_actual_workflow(history, event, base, head)
    assert result.returncode == 1 and untagged in result.stdout


def test_new_branch_fallback_still_checks_all_reachable_history(history):
    _, old, _, untagged, head = history
    result = run_actual_workflow(history, "push", "0" * 40, head)
    assert result.returncode == 1 and old in result.stdout and untagged in result.stdout
