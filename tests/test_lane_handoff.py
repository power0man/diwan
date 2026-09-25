"""مسُّ مسارِ غيرك بلا «تسليم:» يُردّ آليًّا (ك٣٠).

يُبنى مستودعُ git مؤقّت بإيداعاتٍ تحمل ذيلَ `Diwan-Agent` لعائلاتٍ مختلفة وتمسّ مساراتٍ
مختلفة، ويُثبَت أن الاعتراض يقع على من مسّ مسارَ غيره صامتًا وحده: لا على من أعلن التسليم،
ولا على من مسّ مسارَه أو مسارًا بلا مالك، ولا على المالك، ولا على ما سبق وقتَ التفعيل.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import pytest  # noqa: E402

import agent_attribution  # noqa: E402
import lane_handoff as lh  # noqa: E402

LANES = lh.load_lanes(json.dumps({
    "schema_version": 1, "handoff_marker": "تسليم:", "enforced_from": "2026-09-25T00:00:00Z",
    "lanes": {"anthropic": ["evaluation/", "core/run.py"], "openai": ["webui/", "core/sandbox.py"],
              "google": ["core/linguistics/"]},
    "limits": ["declared_not_verified"],
}))
REGISTRY = agent_attribution.load_registry((ROOT / "registry" / "agents.json").read_bytes())
TRAILERS = ("Co-Authored-By: someone <noreply@example.org>\nDiwan-Agent: {agent}\n")


def _commit(repo: Path, path: str, agent: str, *, body: str = "", when: str = "2026-09-25T15:00:00+00:00") -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(target.read_text() + "x\n" if target.exists() else "x\n")
    env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.org",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.org")
    message = f"يمسّ {path}\n\n" + (body + "\n\n" if body else "") + TRAILERS.format(agent=agent)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", message], check=True, env=env)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    _commit(repo, "README.md", "human/hussain-alrabighi", when="2026-09-24T00:00:00+00:00")
    return repo


def test_only_a_silent_touch_of_another_lane_is_refused(tmp_path):
    repo = _repo(tmp_path)
    exempt = _commit(repo, "webui/a.js", "anthropic/claude-fable-5-1", when="2026-09-24T12:00:00+00:00")
    silent = _commit(repo, "webui/a.js", "anthropic/claude-fable-5-1")
    declared = _commit(repo, "webui/b.js", "anthropic/claude-fable-5-1",
                       body="تسليم: المسارُ لـGPT، والتعديلُ بتفويض المالك.")
    silent_openai = _commit(repo, "evaluation/x.py", "openai/codex")
    unlisted = _commit(repo, "docs/x.md", "openai/codex")
    own = _commit(repo, "core/linguistics/z.py", "google/gemini-antigravity")
    owner = _commit(repo, "evaluation/y.py", "human/hussain-alrabighi")
    report = lh.check_range(repo, "HEAD~7..HEAD", lanes=LANES, registry=REGISTRY)
    assert report["status"] == "failed" and report["code"] == "handoff_missing"
    assert {f["sha"] for f in report["findings"]} == {silent, silent_openai}
    assert all(f["code"] == "lane_handoff_missing" for f in report["findings"])
    by_sha = {f["sha"]: f for f in report["findings"]}
    assert by_sha[silent]["paths"] == [{"path": "webui/a.js", "lane": "openai"}]
    assert by_sha[silent_openai]["agent"] == "openai/codex"
    for sha in (exempt, declared, unlisted, own, owner):
        assert sha not in by_sha, sha


def test_a_clean_range_passes_and_counts_its_commits(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "evaluation/a.py", "anthropic/claude-fable-5-1")
    _commit(repo, "webui/a.js", "openai/codex")
    report = lh.check_range(repo, "HEAD~2..HEAD", lanes=LANES, registry=REGISTRY)
    assert report["status"] == "passed" and report["checked"] == 2 and report["findings"] == []


def test_the_longest_prefix_owns_the_path():
    lanes = lh.load_lanes(json.dumps({
        "schema_version": 1, "handoff_marker": "تسليم:", "enforced_from": "2026-09-25T00:00:00Z",
        "lanes": {"anthropic": ["core/"], "google": ["core/linguistics/"]}, "limits": ["x"]}))
    assert lh.lane_of("core/linguistics/roots.py", lanes) == "google"
    assert lh.lane_of("core/run.py", lanes) == "anthropic"
    assert lh.lane_of("docs/x.md", lanes) is None


def test_the_repository_lane_map_is_well_formed_and_matches_the_roles_table():
    lanes = lh.load_lanes((ROOT / "registry" / "lanes.json").read_bytes())
    assert set(lanes["lanes"]) == {"anthropic", "openai", "google"}
    assert lh.lane_of("evaluation/benchmark_metrics.py", lanes) == "anthropic"
    assert lh.lane_of("core/sandbox.py", lanes) == "openai"
    assert lh.lane_of("core/linguistics/roots.py", lanes) == "google"
    assert lh.lane_of("docs/STATUS.md", lanes) is None


@pytest.mark.parametrize("path,family", [
    # البلوكاتُ ب١–ب١٢ (ق٦١، docs/PROJECT-PLAN-20260925.md §٢)، ومنها مساراتُ قدراتٍ لم تُبنَ بعد
    ("core/ledger.py", "anthropic"), ("agent/web_search.py", "anthropic"), ("memory/store.py", "anthropic"),
    ("tools/external_review.py", "anthropic"), (".github/workflows/verify-hosted.yml", "anthropic"),
    ("services/assistant_workspace.py", "openai"), ("workspace_tools/files.py", "openai"),
    ("analysis/tool.py", "openai"), ("documents/export.py", "openai"),
    ("services/translate.py", "google"), ("core/vector_retrieval.py", "google"), ("nodes/maritime/node.py", "google"),
    ("services/media_assistant.py", "google"), ("media/image_gen.py", "google"),
    ("docs/PROJECT-PLAN-20260925.md", None), ("AGENTS.md", None), ("tools/chat.py", None),
])
def test_every_block_of_the_project_plan_has_its_owning_family(path, family):
    lanes = lh.load_lanes((ROOT / "registry" / "lanes.json").read_bytes())
    assert lh.lane_of(path, lanes) == family, path


def test_a_silent_touch_of_a_new_capability_lane_is_refused_by_the_real_map(tmp_path):
    """الذاكرةُ (ب٤) لم تُبنَ بعد، ومسارُها محكومٌ من أول سطر: مسُّها من عائلةٍ أخرى بلا «تسليم:» يُردّ."""
    lanes = lh.load_lanes((ROOT / "registry" / "lanes.json").read_bytes())
    repo = _repo(tmp_path)
    silent = _commit(repo, "memory/store.py", "openai/codex")
    declared = _commit(repo, "memory/index.py", "openai/codex", body="تسليم: ب٤ لـ anthropic، بتفويض المالك.")
    report = lh.check_range(repo, "HEAD~2..HEAD", lanes=lanes, registry=REGISTRY)
    assert [f["sha"] for f in report["findings"]] == [silent]
    assert report["findings"][0]["paths"] == [{"path": "memory/store.py", "lane": "anthropic"}]
    assert declared not in {f["sha"] for f in report["findings"]}


def test_the_cli_reports_and_exits_nonzero_on_a_silent_touch(tmp_path):
    repo = _repo(tmp_path)
    (repo / "registry").mkdir()
    (repo / "registry" / "lanes.json").write_bytes((ROOT / "registry" / "lanes.json").read_bytes())
    (repo / "registry" / "agents.json").write_bytes((ROOT / "registry" / "agents.json").read_bytes())
    _commit(repo, "webui/a.js", "anthropic/claude-fable-5-1", when="2026-09-25T15:00:00+00:00")
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "lane_handoff.py"),
                             "--repo", str(repo), "--range", "HEAD~1..HEAD"], capture_output=True, text=True)
    assert result.returncode == 1 and '"lane_handoff_missing"' in result.stdout
