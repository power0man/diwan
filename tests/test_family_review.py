"""الدمجُ مشروطٌ بمراجعةٍ من عائلةٍ غير عائلة المؤلّف على الرأس الحاليّ (ك٤٠، ق٦١).

يُثبَت: أن مراجعةَ بوتٍ من عائلةٍ أخرى على الرأس تُنجح الفحص؛ وأن مراجعةَ عائلة المؤلّف، أو حسابِ المالك الذي يكتب به كلُّ عميل،
أو مراجعةً على رأسٍ قديم، لا تُحتسب؛ وأن «طلبَ التغييرات» من عائلةٍ أخرى يُسقطه؛ وأن الخريطةَ لا تقبل غيرَ البوتات ولا تحتسب حالةً
حاجبة؛ وأن المهمّةَ بصلاحية قراءةٍ وحدها؛ وأن عائلاتِ المطوِّرين في مراجعة البنوك تُشتقّ من السجل.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import agent_attribution  # noqa: E402
import family_review as fr  # noqa: E402

REGISTRY = agent_attribution.load_registry((ROOT / "registry" / "agents.json").read_bytes())
REVIEWERS = fr.load_reviewers((ROOT / "registry" / "reviewers.json").read_bytes())
HEAD, OLD = "a" * 40, "b" * 40


def review(login, state, commit=HEAD):
    return {"user": {"login": login}, "state": state, "commit_id": commit}


def commit(agent):
    return {"sha": "c" * 40, "committed_at": 1_800_000_000,
            "message": f"عمل\n\nCo-Authored-By: x <noreply@example.org>\nDiwan-Agent: {agent}\n"}


# — الحكم —

def test_another_family_reviewing_the_current_head_passes():
    report = fr.evaluate({"anthropic"}, [review("chatgpt-codex-connector[bot]", "COMMENTED")], HEAD, REVIEWERS)
    assert report["status"] == "passed" and report["code"] == "reviewed_by_another_family"
    assert report["counted"] == ["chatgpt-codex-connector[bot]"]


@pytest.mark.parametrize("reviews,code", [
    ([review("claude[bot]", "APPROVED")], "reviewed_only_by_the_author_family"),
    ([review("power0man", "APPROVED")], "no_review_from_another_family"),
    ([review("chatgpt-codex-connector[bot]", "APPROVED", OLD)], "no_review_from_another_family"),
    ([review("gemini-code-assist[bot]", "APPROVED")], "no_review_from_another_family"),   # أُلغي Gemini (ق٦٥)
    ([review("unknown-bot[bot]", "APPROVED")], "no_review_from_another_family"),
    ([], "no_review_from_another_family"),
])
def test_what_does_not_count_as_another_family_review(reviews, code):
    report = fr.evaluate({"anthropic"}, reviews, HEAD, REVIEWERS)
    assert report["status"] == "failed" and report["code"] == code


def test_changes_requested_by_another_family_blocks_even_with_an_approval():
    reviews = [review("claude[bot]", "APPROVED"), review("chatgpt-codex-connector[bot]", "CHANGES_REQUESTED")]
    report = fr.evaluate({"google"}, reviews, HEAD, REVIEWERS)
    assert report["status"] == "failed" and report["blocking"] == ["chatgpt-codex-connector[bot]"]


def test_the_latest_review_on_the_head_decides():
    later_approval = [review("chatgpt-codex-connector[bot]", "CHANGES_REQUESTED"), review("chatgpt-codex-connector[bot]", "APPROVED")]
    assert fr.evaluate({"anthropic"}, later_approval, HEAD, REVIEWERS)["status"] == "passed"
    later_block = list(reversed(later_approval))
    assert fr.evaluate({"anthropic"}, later_block, HEAD, REVIEWERS)["code"] == "changes_requested_by_another_family"


def test_a_pull_request_from_two_families_needs_a_third():
    both = {"anthropic", "openai"}
    assert fr.evaluate(both, [review("chatgpt-codex-connector[bot]", "APPROVED")], HEAD, REVIEWERS)["status"] == "failed"
    assert fr.evaluate(both, [review("claude[bot]", "APPROVED")], HEAD, REVIEWERS)["status"] == "failed"
    assert fr.evaluate({"anthropic", "google"}, [review("chatgpt-codex-connector[bot]", "COMMENTED")], HEAD, REVIEWERS)["status"] == "passed"


def test_author_families_come_from_the_trailer_not_the_account():
    commits = [commit("anthropic/claude-opus-5-5"), commit("openai/codex"), commit("nobody/unregistered")]
    assert fr.author_families(commits, REGISTRY) == {"anthropic", "openai"}


# — الخريطة لا توسّع الثقة —

@pytest.mark.parametrize("mutate,code", [
    (lambda d: d["reviewers"].__setitem__("power0man", "human"), "reviewer_not_a_bot"),
    (lambda d: d["reviewers"].__setitem__("someone", "google"), "reviewer_not_a_bot"),
    (lambda d: d.__setitem__("counted_states", ["APPROVED", "CHANGES_REQUESTED"]), "counted_state_not_allowed"),
    (lambda d: d["reviewers"].__setitem__("x[bot]", "human"), "reviewers_schema_invalid"),
])
def test_the_reviewer_map_refuses_what_widens_trust(mutate, code):
    data = json.loads((ROOT / "registry" / "reviewers.json").read_text(encoding="utf-8"))
    mutate(data)
    with pytest.raises(fr.ReviewError) as refused:
        fr.load_reviewers(json.dumps(data))
    assert refused.value.code == code


def test_the_repository_map_counts_one_bot_per_developer_family_and_no_gemini():
    assert set(REVIEWERS["reviewers"].values()) == {"anthropic", "openai"}
    assert not any("gemini" in login for login in REVIEWERS["reviewers"])
    assert all(login.endswith("[bot]") for login in REVIEWERS["reviewers"])


# — سطرُ الأوامر على مستودعٍ حقيقي —

def _git(repo, *argv, env=None):
    return subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True, text=True, env=env).stdout.strip()


def test_the_cli_reads_the_range_and_a_reviews_file(tmp_path):
    repo = tmp_path / "repo"
    (repo / "registry").mkdir(parents=True)
    for name in ("agents.json", "reviewers.json"):
        (repo / "registry" / name).write_bytes((ROOT / "registry" / name).read_bytes())
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.org",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.org")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "base", env=env)
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "x.txt").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "عمل\n\nDiwan-Agent: anthropic/claude-opus-5-5", env=env)
    head = _git(repo, "rev-parse", "HEAD")
    reviews = tmp_path / "reviews.json"

    def run(items):
        reviews.write_text(json.dumps(items), encoding="utf-8")
        return subprocess.run([sys.executable, str(ROOT / "tools" / "family_review.py"), "--repo", str(repo),
                               "--range", f"{base}..{head}", "--head", head, "--reviews-json", str(reviews)],
                              capture_output=True, text=True)
    passed = run([review("chatgpt-codex-connector[bot]", "COMMENTED", head)])
    assert passed.returncode == 0 and '"reviewed_by_another_family"' in passed.stdout
    same = run([review("claude[bot]", "APPROVED", head)])
    assert same.returncode == 1 and '"reviewed_only_by_the_author_family"' in same.stdout


# — المهمّة —

def test_the_workflow_reads_only_and_runs_on_reviews():
    workflow = (ROOT / ".github" / "workflows" / "family-review.yml").read_text(encoding="utf-8")
    assert "pull_request_review:" in workflow and "pull_request:" in workflow
    assert "contents: read" in workflow and "pull-requests: read" in workflow
    assert ": write" not in workflow and "secrets." not in workflow
    assert "persist-credentials: false" in workflow and "fetch-depth: 0" in workflow
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow
    assert "  family-review:\n" in workflow, "اسمُ الفحص الذي تطلبه حمايةُ main"


# — عائلاتُ المطوِّرين في مراجعة البنوك تتبع السجل —

def test_developer_families_follow_the_agent_registry(tmp_path):
    from evaluation import external_review as er
    assert set(er.DEVELOPER_FAMILIES) == {a.split("/")[0] for a in REGISTRY["agents"] if not a.startswith("human/")}
    registry = json.loads((ROOT / "registry" / "agents.json").read_text(encoding="utf-8"))
    registry["agents"]["mistral/devstral-local"] = {"surface": "OpenCode", "admitted": "2026-09-26"}
    extended = tmp_path / "agents.json"
    extended.write_text(json.dumps(registry), encoding="utf-8")
    assert "mistral" in er.developer_families(extended)
    assert "human" not in er.developer_families(extended)


# — تعليقُ المراجعة النظيفة من Codex (ق٦٥) —

def clean(login, commit, text="Codex Review: Didn't find any major issues. More of your lovely PRs please."):
    return {"user": {"login": login}, "body": f"{text}\n\n**Reviewed commit:** `{commit}`\n\n<details>…</details>"}


def test_a_clean_codex_comment_on_the_head_counts():
    reviews = fr.clean_comment_reviews([clean("chatgpt-codex-connector[bot]", HEAD[:10])], HEAD)
    report = fr.evaluate({"anthropic"}, reviews, HEAD, REVIEWERS)
    assert report["status"] == "passed" and report["counted"] == ["chatgpt-codex-connector[bot]"]


@pytest.mark.parametrize("comment", [
    clean("chatgpt-codex-connector[bot]", OLD[:10]),                                   # إيداعٌ قديم
    clean("power0man", HEAD[:10]),                                                   # حسابُ المالك يكتب به كلُّ عميل
    clean("someone[bot]", HEAD[:10]),                                                # بوتٌ غير مدرَج
    clean("chatgpt-codex-connector[bot]", HEAD[:10], text="Codex Review: here are some suggestions"),  # ليست نظيفة
    {"user": {"login": "chatgpt-codex-connector[bot]"}, "body": "Didn't find any major issues"},    # بلا إيداعٍ مسمًّى
])
def test_what_a_clean_comment_cannot_count_for(comment):
    reviews = fr.clean_comment_reviews([comment], HEAD)
    assert fr.evaluate({"anthropic"}, reviews, HEAD, REVIEWERS)["status"] == "failed"


def test_the_cli_reads_comments_from_a_reviews_file(tmp_path):
    repo = tmp_path / "repo"
    (repo / "registry").mkdir(parents=True)
    for name in ("agents.json", "reviewers.json"):
        (repo / "registry" / name).write_bytes((ROOT / "registry" / name).read_bytes())
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.org", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.org")
    _git(repo, "init", "-q", "-b", "main"); _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "base", env=env)
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "x.txt").write_text("x"); _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "عمل\n\nDiwan-Agent: anthropic/claude-opus-5-5", env=env)
    head = _git(repo, "rev-parse", "HEAD")
    data = tmp_path / "reviews.json"
    data.write_text(json.dumps({"reviews": [], "comments": [clean("chatgpt-codex-connector[bot]", head[:10])]}), encoding="utf-8")
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "family_review.py"), "--repo", str(repo),
                          "--range", f"{base}..{head}", "--head", head, "--reviews-json", str(data)], capture_output=True, text=True)
    assert out.returncode == 0 and '"reviewed_by_another_family"' in out.stdout


def test_the_workflow_waits_for_the_reviewer_within_its_timeout():
    workflow = (ROOT / ".github" / "workflows" / "family-review.yml").read_text(encoding="utf-8")
    assert "--wait-seconds 900" in workflow and "timeout-minutes: 20" in workflow


def test_only_a_listed_bot_can_produce_a_clean_comment_review():
    assert fr.clean_comment_reviews([clean("power0man", HEAD[:10]), clean("someone[bot]", HEAD[:10])], HEAD) == []
