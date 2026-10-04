"""التشغيلُ اليدويّ لـverify-hosted يفحص الفرعَ كلَّه لا رأسَه (تدقيقٌ لاحقٌ لـ85f2317).

كان `workflow_dispatch` يأخذ المدى `RANGE_HEAD^!`، فيفحص الإسنادُ والمساراتُ الإيداعَ الأخيرَ وحده ويجري فاحصُ الطفرات
بـ`--all` بلا شروط المدى؛ فتشغيلٌ يدويّ على الرأس نفسِه يُخضِر `verify` بعد أن سقط على الطلب لإيداعٍ أقدم بلا ذيل.
يُشغَّل هنا نصُّ الخطوة نفسُه كما في الملف، في مستودعٍ مؤقّت ومفسّرٍ بديلٍ يسجّل وسائطه، بلا مشغّل Actions ولا شبكة.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOSTED = ROOT / ".github/workflows/verify-hosted.yml"
STEP = "      - name: Every commit names its producing agent\n"


def _step_script() -> str:
    text = HOSTED.read_text(encoding="utf-8")
    assert text.count(STEP) == 1
    lines = text.split(STEP, 1)[1].splitlines()
    start = lines.index("        run: |") + 1
    body = []
    for line in lines[start:]:
        if line.strip() and not line.startswith(" " * 10):
            break
        body.append(line[10:])
    return "\n".join(body) + "\n"


def _repo(tmp_path: Path) -> tuple[Path, dict]:
    env = {**os.environ, "GIT_AUTHOR_NAME": "F", "GIT_AUTHOR_EMAIL": "f@example.invalid",
           "GIT_COMMITTER_NAME": "F", "GIT_COMMITTER_EMAIL": "f@example.invalid"}

    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, env=env, check=True, capture_output=True,
                              text=True).stdout.strip()

    git("init", "-q", "-b", "main")
    shas = {}
    for name in ("base", "old", "tip"):
        (tmp_path / f"{name}.txt").write_text(name)
        git("add", "-A")
        git("commit", "-qm", name)
        shas[name] = git("rev-parse", "HEAD")
        if name == "base":
            git("update-ref", "refs/remotes/origin/main", shas["base"])
            git("checkout", "-qb", "feature")
    stub = tmp_path / ".venv/bin/python"
    stub.parent.mkdir(parents=True)
    stub.write_text('#!/bin/sh\necho "$@" >> "$CALLS"\n', encoding="utf-8")
    stub.chmod(0o755)
    return tmp_path, shas


def _run(repo: Path, head: str, event: str = "workflow_dispatch", base: str = "") -> tuple[str, str]:
    github_env, calls = repo / "github.env", repo / "calls.log"
    github_env.write_text(""), calls.write_text("")
    env = {**os.environ, "EVENT_NAME": event, "RANGE_BASE": base, "RANGE_HEAD": head,
           "GITHUB_ENV": str(github_env), "CALLS": str(calls)}
    subprocess.run(["bash", "-c", _step_script()], cwd=repo, env=env, check=True, capture_output=True, text=True)
    return github_env.read_text().strip(), calls.read_text().strip()


def test_a_manual_run_on_a_branch_checks_every_commit_since_main_not_only_the_head(tmp_path):
    repo, shas = _repo(tmp_path)
    recorded, calls = _run(repo, shas["tip"])
    assert recorded == f"RANGE={shas['base']}..{shas['tip']}"
    assert calls == f"tools/agent_attribution.py --range {shas['base']}..{shas['tip']}"


def test_a_manual_run_on_main_itself_keeps_the_head_commit(tmp_path):
    repo, shas = _repo(tmp_path)
    recorded, _ = _run(repo, shas["base"])
    assert recorded == f"RANGE={shas['base']}^!"


def test_a_pull_request_run_keeps_its_base_to_head_range(tmp_path):
    repo, shas = _repo(tmp_path)
    recorded, _ = _run(repo, shas["tip"], event="pull_request", base=shas["old"])
    assert recorded == f"RANGE={shas['old']}..{shas['tip']}"
