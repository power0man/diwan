"""محوِّلُ مشروع ديوان على مشغِّلٍ مزيَّف لـ`gh`: الإذنُ من حدث الوسم، والتجميد، وجدولُ ق٧٥، وعائلاتُ الذيول، وحالةُ الفحوص."""
from __future__ import annotations

import json
from pathlib import Path

from team.projects.base import Issue, PullRequest
from team.projects.diwan import DiwanProject

ROOT = Path(__file__).resolve().parents[1]


class Done:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


def _project(payloads: dict):
    def runner(argv, **kw):
        key = " ".join(argv[:3])
        for prefix, payload in payloads.items():
            if key.startswith(prefix):
                return Done(json.dumps(payload) if not isinstance(payload, str) else payload)
        return Done("", 1)
    return DiwanProject(runner=runner, root=ROOT)


def test_ready_is_granted_only_by_the_owner_s_last_label_event():
    def events(actor, last="labeled"):
        return [{"event": "labeled", "label": {"name": "ready:anthropic"}, "actor": {"login": actor}},
                {"event": last, "label": {"name": "ready:anthropic"}, "actor": {"login": actor}}]
    assert _project({"gh api --paginate": events("power0man")}).ready_granted(1, "anthropic")
    assert not _project({"gh api --paginate": events("power0man", "unlabeled")}).ready_granted(1, "anthropic")
    assert not _project({"gh api --paginate": events("someone-else")}).ready_granted(1, "anthropic")


def _label_events(name, actor, last="labeled"):
    return [{"event": "labeled", "label": {"name": name}, "actor": {"login": actor}},
            {"event": last, "label": {"name": name}, "actor": {"login": actor}}]


def test_frozen_tasks_and_core_paths_are_refused_unless_excepted():
    project = _project({"gh api --paginate": _label_events("measurement", "power0man")})
    assert project.frozen_findings(Issue(1, "[ك١٧] بنك v1.2", "", ("task",))) == ["frozen_task:ك١٧"]
    assert project.frozen_findings(Issue(2, "إصلاح", "يمسّ core/run.py", ("task",))) == ["frozen_core_path:core/run.py"]
    assert project.frozen_findings(Issue(3, "[ك١٧] بنك", "core/run.py", ("task", "measurement"))) == []
    assert project.frozen_findings(Issue(4, "عادية", "لا شيء", ("task",))) == []


def test_a_frozen_exception_label_counts_only_when_the_owner_set_it():
    frozen = Issue(3, "[ك١٧] بنك", "core/run.py", ("task", "measurement"))
    assert _project({"gh api --paginate": _label_events("measurement", "someone-else")}).frozen_findings(frozen) != []
    assert _project({"gh api --paginate": _label_events("measurement", "power0man", "unlabeled")}).frozen_findings(frozen) != []
    assert _project({"gh api --paginate": _label_events("measurement", "power0man")}).frozen_findings(frozen) == []


def test_review_policy_follows_q75():
    project = _project({})
    assert project.review_policy({"anthropic"}).candidates == ["codex"]
    assert project.review_policy({"openai"}).candidates == ["claude"]
    assert project.review_policy({"google"}).candidates == ["claude", "codex"]
    assert project.review_policy({"anthropic", "openai"}).candidates == []
    assert "gemini" in project.review_policy({"anthropic"}).never


def test_author_families_come_from_diwan_agent_trailers():
    project = _project({})
    pull = PullRequest(1, "a" * 40, "main", "team/1-anthropic", 1, commit_messages=(
        "عمل\n\nDiwan-Agent: anthropic/claude-fable-5-1", "تصحيح\n\nDiwan-Agent: openai/codex\nCo-Authored-By: x <x@example.org>"))
    assert project.author_families(pull) == {"anthropic", "openai"}


def _runs(*items, status="completed"):
    return {"gh api repos/power0man/diwan/commits/h/check-runs": {"check_runs": [{"name": n, "status": status, "conclusion": c} for n, c in items]}}


def test_checks_are_mapped_to_success_failure_pending_or_none():
    assert _project(_runs(("verify-hosted", "success"), ("container-smoke", "skipped"))).checks("h") == "success"
    assert _project(_runs(("verify-hosted", "success"), ("x", "failure"))).checks("h") == "failure"
    assert _project(_runs(("verify-hosted", None), status="in_progress")).checks("h") == "pending"
    assert _project(_runs()).checks("h") == "none"


def test_an_unrelated_or_skipped_check_does_not_validate_the_head():
    assert _project(_runs(("unrelated", "success"))).checks("h") == "pending"
    assert _project(_runs(("verify-hosted", "skipped"), ("other", "skipped"))).checks("h") == "pending"


def test_proof_of_acceptance_fetches_main_before_checking_ancestry():
    seen = []
    class Done:
        def __init__(self): self.returncode, self.stdout, self.stderr = 0, "", ""
    def runner(argv, **kw):
        seen.append(argv[3] if argv[0] == "git" else argv[0])
        return Done()
    project = DiwanProject(runner=runner, root=ROOT)
    pull = PullRequest(1, "a" * 40, "main", "team/1-anthropic", 1, state="merged", merge_sha="m" * 40)
    assert project.proof_of_acceptance(pull) == "m" * 40
    assert seen[:2] == ["fetch", "merge-base"]


def test_issue_is_read_from_the_body_then_the_branch():
    assert DiwanProject.issue_of("…\n\nRefs #341\n", "x") == 341
    assert DiwanProject.issue_of("", "team/342-anthropic") == 342
    assert DiwanProject.issue_of("", "claude/other") is None
