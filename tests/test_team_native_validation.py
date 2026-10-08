"""Synthetic native-review/CI evidence: no live models, GitHub, owner data or acceptance."""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from core.ledger import LedgerCorrupt
from team.dispatch import Dispatcher, Refusal
from team.ledger import TeamLedger, TransitionError
from team.projects.base import NativeReview, ProjectAdapter, PullRequest
from team.projects.diwan import DiwanProject, GhError, TRUSTED_REVIEW_FILES
from team.review import Reviewer, Refusal as ReviewRefusal, record_calibration
from tests.team_fakes import FakeAdapter, FakeProject
from tests.test_team_review import _setup as _review_setup, _dispatched as _review_dispatched

ROOT = Path(__file__).resolve().parents[1]
HEAD = "a" * 40
NOW = "2026-10-08T10:00:00+00:00"
CALIBRATION = {"calibrated_at": NOW, "defects_planted": 2, "defects_caught": 1,
               "false_alarms": 0, "model": "fixture-model"}


def _dispatch(tmp_path, **changes):
    pull = PullRequest(9, HEAD, "main", "team/41-anthropic", 41,
                       ("work\n\nDiwan-Agent: anthropic/claude-fable-5-1",))
    project = FakeProject(pulls={9: pull}, checks_by_head={HEAD: "success"})
    ledger = TeamLedger(tmp_path / "home" / "dispatch.jsonl", clock=lambda: NOW)
    ledger.append(41, "dispatched", brief_sha256="b" * 64, worker="claude", family="anthropic", branch=pull.branch)
    ledger.append(41, "claimed", pid=1, started_at=NOW)
    ledger.append(41, "completed", head_sha=HEAD, branch=pull.branch, pr=9)
    evidence = {"head_sha": HEAD, "pr": 9, "review_ref": "fixture-comment", "reviewer": "codex",
                "reviewer_family": "openai", "reviewer_model": "fixture-model", "reviewer_identity": "openai/codex",
                "reviewer_calibration": dict(CALIBRATION), "review_execution_id": "c" * 32, "verdict": "pass"}
    evidence.update(changes)
    ledger.append(41, "reviewed_awaiting_validation", **evidence)
    dispatcher = Dispatcher(project, FakeAdapter(), ledger, tmp_path, home=tmp_path / "home", clock=lambda: NOW)
    return dispatcher, project, ledger, evidence


def test_original_review_records_explicit_evidence_and_survives_validation(tmp_path):
    dispatcher, project, ledger, _ = _dispatch(tmp_path)
    reviewer = Reviewer(project, {}, ledger, tmp_path, home=dispatcher.home, clock=lambda: NOW)
    record_calibration(dispatcher.home, "codex", caught=1, of=2, false_alarms=0, model="fixture-model", clock=lambda: NOW)
    adapter = FakeAdapter(name="codex", family="openai")
    reviewer._record(project.pulls[9], adapter, HEAD, "new-fixture-comment", "pass", execution_id="d" * 32)
    recorded = ledger.last(41)
    assert recorded["reviewer_model"] == "fixture-model" and recorded["reviewer_identity"] == "openai/codex"
    assert recorded["reviewer_calibration"] == CALIBRATION and recorded["review_execution_id"] == "d" * 32
    assert dispatcher.native_review(41, project.pulls[9]) is not None
    assert dispatcher.validate(41)["status"] == "validated"
    assert ledger.main_state(41)["state"] == "verified"
    assert ledger.main_state(41)["reviewer_model"] == "fixture-model"


def test_counted_invocations_record_fresh_execution_without_reusing_a_model_session(tmp_path):
    reviewer, project, _adapters, ledger = _review_setup(tmp_path)
    _review_dispatched(ledger, project.pulls[9].head_sha)
    assert reviewer.review(9, execute=True)["status"] == "verified"
    first = ledger.last(41)
    assert len(first["review_execution_id"]) == 32 and first["reviewer_model"] == "fixture-model"
    assert reviewer.review(9, execute=True)["status"] == "reviewed_awaiting_validation"
    assert ledger.last(41)["review_execution_id"] != first["review_execution_id"]


def test_reviewer_changes_to_the_detached_tree_cannot_be_published_or_counted(tmp_path):
    reviewer, project, adapters, ledger = _review_setup(tmp_path)
    _review_dispatched(ledger, project.pulls[9].head_sha)
    def write_during_review(argv, prompt, cwd, timeout):
        (cwd / "unexpected.txt").write_text("changed")
        return 0, "الحكم: صامد", ""
    adapters["codex"].run_review = write_during_review
    with pytest.raises(ReviewRefusal, match="review_worktree_changed"):
        reviewer.review(9, execute=True)
    assert project.comments == [] and ledger.main_state(41)["state"] == "validated"


@pytest.mark.parametrize("changes", [
    {"head_sha": "b" * 40}, {"attempt": 2}, {"pr": 10}, {"verdict": "unknown"},
    {"reviewer_model": ""}, {"reviewer_model": "different-model"}, {"reviewer_identity": "human/hussain-alrabighi"},
    {"reviewer_family": "anthropic"}, {"reviewer_family": "google"}, {"reviewer": "gemini"}, {"review_execution_id": ""}, {"review_ref": 1},
    {"reviewer_calibration": {}},
    {"reviewer_calibration": CALIBRATION | {"calibrated_at": "2026-08-01T00:00:00+00:00"}},
    {"reviewer_calibration": CALIBRATION | {"calibrated_at": "2099-01-01T00:00:00+00:00"}},
    {"reviewer_calibration": CALIBRATION | {"defects_caught": 0}},
])
def test_incomplete_stale_or_wrong_native_evidence_never_verifies(tmp_path, changes):
    dispatcher, project, ledger, _ = _dispatch(tmp_path, **changes)
    assert dispatcher.native_review(41, project.pulls[9]) is None
    assert dispatcher.validate(41)["status"] == "validated"
    assert ledger.main_state(41)["state"] == "validated"


@pytest.mark.parametrize("changes", [{"number": 10}, {"issue": 42}, {"head_sha": "b" * 40}, {"branch": "other"},
    {"commit_messages": ("work\n\nDiwan-Agent: anthropic/x", "work\n\nDiwan-Agent: openai/codex")},
    {"commit_messages": ()}])
def test_native_evidence_is_bound_to_current_pull_branch_and_all_authors(tmp_path, changes):
    dispatcher, project, _ledger, _ = _dispatch(tmp_path)
    assert dispatcher.native_review(41, replace(project.pulls[9], **changes)) is None


@pytest.mark.parametrize("state", ["review_rejected", "review_uncalibrated"])
def test_a_newer_negative_review_blocks_an_older_native_pass(tmp_path, state):
    dispatcher, project, ledger, evidence = _dispatch(tmp_path)
    ledger.append(41, state, **(evidence | {"verdict": "reject", "review_ref": "newer"}))
    assert dispatcher.native_review(41, project.pulls[9]) is None
    dispatcher.validate(41)
    assert ledger.main_state(41)["state"] == "validated"


def test_old_attempt_records_do_not_override_current_attempt_and_takeover_blocks(tmp_path):
    dispatcher, project, ledger, evidence = _dispatch(tmp_path)
    ledger.clock = lambda: "2026-10-10T11:00:00+00:00"
    ledger.append(41, "expired", last_activity_at=NOW)
    ledger.append(41, "takeover", lease_expired_at=ledger.clock(),
                  absence_proof={"no_process": True, "no_session": True, "no_new_commits": True}, owner_authorization="fixture")
    assert dispatcher.native_review(41, project.pulls[9]) is None


    ledger.append(41, "dispatched", brief_sha256="d" * 64, worker="claude", family="anthropic", branch=project.pulls[9].branch)
    ledger.append(41, "claimed", pid=2, started_at=ledger.clock())
    ledger.append(41, "completed", head_sha=HEAD, branch=project.pulls[9].branch, pr=9)
    ledger.append(41, "reviewed_awaiting_validation", **(evidence | {"attempt": 1}))
    assert dispatcher.latest_review(41, HEAD) is None
    assert dispatcher.native_review(41, project.pulls[9]) is None


def test_native_evidence_checks_the_chain_and_does_not_trust_legacy_verified(tmp_path):
    dispatcher, project, ledger, _ = _dispatch(tmp_path)
    ledger.path.write_text(ledger.path.read_text().replace('"fixture-model"', '"tampered-model"'))
    with pytest.raises(LedgerCorrupt):
        dispatcher.native_review(41, project.pulls[9])


def test_missing_original_evidence_blocks_acceptance_without_rewriting_history(tmp_path):
    dispatcher, project, ledger, _ = _dispatch(tmp_path, reviewer_model="")
    ledger.append(41, "validated", head_sha=HEAD, checks_ref="fixture-checks")
    ledger.append(41, "verified", head_sha=HEAD, review_ref="legacy", reviewer="codex", reviewer_family="openai")
    project.pulls[9] = replace(project.pulls[9], state="merged", merge_sha="e" * 40)
    with pytest.raises(Refusal, match="native_review_unproven"):
        dispatcher.accept(41)
    assert ledger.main_state(41)["state"] == "verified"


def test_acceptance_rechecks_ci_even_when_the_original_native_pass_is_valid(tmp_path):
    dispatcher, project, ledger, _ = _dispatch(tmp_path)
    dispatcher.validate(41)
    project.pulls[9] = replace(project.pulls[9], state="merged", merge_sha="e" * 40)
    project.checks_by_head[HEAD] = "failure"
    with pytest.raises(Refusal, match="checks_not_validated_at_acceptance"):
        dispatcher.accept(41)
    assert ledger.main_state(41)["state"] == "verified"


def test_a_head_changed_between_sync_and_ci_snapshot_is_a_named_refusal(tmp_path):
    dispatcher, project, _ledger, _ = _dispatch(tmp_path)
    original = project.pulls[9]
    seen = []
    def advancing_pull(number):
        seen.append(number)
        return original if len(seen) == 1 else replace(original, head_sha="b" * 40)
    project.pull = advancing_pull
    with pytest.raises(Refusal, match="head_changed_during_validation"):
        dispatcher.validate(41)


class Done:
    def __init__(self, payload=None, returncode=0):
        self.stdout = json.dumps(payload) if payload is not None else ""
        self.stderr, self.returncode = "", returncode


def _diwan(tmp_path):
    repo = "power0man/diwan"
    pull = PullRequest(9, HEAD, "main", "team/41-anthropic", 41,
                       ("work\n\nDiwan-Agent: anthropic/claude-fable-5-1",))
    ref = f"https://github.com/{repo}/pull/9#issuecomment-123"
    proof = NativeReview(9, HEAD, "codex", "openai", "gpt-6.1-sol", "openai/codex", ref, ("anthropic",))
    runs = [{"name": name, "head_sha": HEAD, "status": "completed", "conclusion": conclusion,
             "id": 55,
             "app": {"id": 15368, "slug": "github-actions"}, "check_suite": {"id": 44},
             "details_url": f"https://github.com/{repo}/actions/runs/11/job/22"}
            for name, conclusion in (("verify", "success"), ("verify-hosted", "success"),
                                     ("container-smoke", "success"), ("family-review", "failure"))]
    prefix = f"repos/{repo}/"
    payloads = {
        prefix + f"commits/{HEAD}/check-runs": {"check_runs": runs},
        prefix + f"commits/{HEAD}/check-runs?per_page=100": [{"check_runs": runs}],
        prefix + "commits/main": {"sha": "f" * 40},
        prefix + "pulls/9/commits": [[{"commit": {"message": pull.commit_messages[0]}}]],
        prefix + "pulls/9/reviews": [[]], prefix + "issues/9/comments": [[]], prefix + "pulls/9/files": [[]],
        prefix + "issues/comments/123": {"html_url": ref, "issue_url": f"https://api.github.com/repos/{repo}/issues/9",
                                          "user": {"login": "power0man"},
                                          "body": f"مراجعةُ codex (openai/codex) على `{HEAD[:12]}`: الحكم: صامد"},
        prefix + "actions/runs/11": {"head_sha": HEAD, "path": ".github/workflows/family-review.yml",
                                      "event": "pull_request", "check_suite_id": 44},
        prefix + "actions/jobs/22": {"head_sha": HEAD, "run_id": 11, "status": "completed", "conclusion": "failure",
            "check_run_url": f"https://api.github.com/repos/{repo}/check-runs/55",
            "steps": [{"name": "Checkout trusted main", "status": "completed", "conclusion": "success"},
                      {"name": "Another family reviewed the current head", "status": "completed", "conclusion": "failure"}]},
        "joblog": "family-review\tAnother family reviewed the current head\t2026-10-08T10:00:00Z " + json.dumps({
            "schema_version": 1, "status": "failed", "code": "no_review_from_another_family", "head": HEAD,
            "author_families": ["anthropic"], "counted": [], "blocking": [], "same_family": []}),
        "graphql": {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [],
                     "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}},
        "pull": {"number": 9, "headRefOid": HEAD, "baseRefName": "main", "headRefName": pull.branch,
                  "body": "Refs #41", "commits": [{"messageHeadline": "work", "messageBody": "Diwan-Agent: anthropic/claude-fable-5-1"}],
                  "state": "OPEN", "url": "https://github.com/power0man/diwan/pull/9"},
    }
    for path in TRUSTED_REVIEW_FILES:
        raw = (ROOT / path).read_bytes()
        payloads[prefix + f"contents/{path}?ref={'f' * 40}"] = {"type": "file", "path": path, "encoding": "base64",
            "content": base64.b64encode(raw).decode(), "sha": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()}
    seen = []
    def runner(argv, **kwargs):
        seen.append(argv)
        if argv[0] != "gh":
            assert argv[1] == "-I"
            return subprocess.run(argv, **kwargs)
        if argv[1:3] == ["pr", "view"]:
            return Done(payloads["pull"])
        if argv[1:3] == ["run", "view"]:
            result = Done()
            result.stdout = payloads["joblog"]
            return result
        path = next((arg for arg in argv[2:] if arg.startswith("repos/") or arg == "graphql"), "")
        return Done(payloads[path]) if path in payloads else Done(returncode=1)
    return DiwanProject(root=ROOT, runner=runner), pull, proof, payloads, runs, seen


def test_only_checked_native_pass_can_replace_missing_bot_and_default_stays_closed(tmp_path):
    project, pull, proof, _payloads, _runs, seen = _diwan(tmp_path)
    assert project.checks(HEAD) == "failure"
    assert project.checks_with_review(pull) == "failure"
    assert project.checks_with_review(pull, native_review=proof) == "success"
    source_calls = [arg for argv in seen for arg in argv if "contents/" in arg]
    assert len(source_calls) == 5 and all(arg.endswith(f"?ref={'f' * 40}") for arg in source_calls)
    assert any(argv[0] != "gh" and argv[1] == "-I" for argv in seen)
    class Minimal(ProjectAdapter):
        def checks(self, head):
            assert head == HEAD
            return "failure"
    assert Minimal().checks_with_review(pull, native_review=proof) == "failure"


@pytest.mark.parametrize("state,login", [("CHANGES_REQUESTED", "chatgpt-codex-connector[bot]"), ("CHANGES_REQUESTED", "claude[bot]")])
def test_native_pass_does_not_replace_rejection_or_same_family_bot_review(tmp_path, state, login):
    project, pull, proof, payloads, _runs, _seen = _diwan(tmp_path)
    payloads["repos/power0man/diwan/pulls/9/reviews"] = [[{"user": {"login": login}, "commit_id": HEAD,
                                                      "state": state, "submitted_at": NOW}]]
    assert project.checks_with_review(pull, native_review=proof) == "failure"


@pytest.mark.parametrize("name,conclusion", [("container-smoke", "failure"), ("verify", "failure"),
    ("verify-hosted", "cancelled"), ("container-smoke", "timed_out"), ("family-review", "cancelled"), ("x", "failure")])
def test_native_pass_never_hides_real_ci_failure(tmp_path, name, conclusion):
    project, pull, proof, _payloads, runs, _seen = _diwan(tmp_path)
    runs.append(runs[0] | {"name": name, "conclusion": conclusion})
    assert project.checks_with_review(pull, native_review=proof) == "failure"


@pytest.mark.parametrize("name", ["verify", "verify-hosted"])
def test_both_required_ci_controls_must_complete_successfully(tmp_path, name):
    project, pull, proof, _payloads, runs, _seen = _diwan(tmp_path)
    target = next(run for run in runs if run["name"] == name)
    target["conclusion"] = "skipped"
    assert project.checks_with_review(pull, native_review=proof) != "success"


@pytest.mark.parametrize("change", ["app", "app-slug", "head", "details", "path", "suite", "event", "changed-workflow"])
def test_check_name_alone_never_proves_trusted_workflow(tmp_path, change):
    project, pull, proof, payloads, runs, _seen = _diwan(tmp_path)
    family = runs[-1]
    if change == "app": family["app"] = {"id": 1, "slug": "github-actions"}
    if change == "app-slug": family["app"] = {"id": 15368, "slug": "attacker"}
    if change == "head": family["head_sha"] = "b" * 40
    if change == "details": family["details_url"] = "https://example.invalid/actions/runs/11"
    workflow = payloads["repos/power0man/diwan/actions/runs/11"]
    if change == "path": workflow["path"] = ".github/workflows/attacker.yml"
    if change == "suite": workflow["check_suite_id"] = 55
    if change == "event": workflow["event"] = "push"
    if change == "changed-workflow": payloads["repos/power0man/diwan/pulls/9/files"] = [[{"filename": ".github/workflows/family-review.yml"}]]
    assert project.checks_with_review(pull, native_review=proof) == "failure"


@pytest.mark.parametrize("change", ["generic", "identity", "sha", "verdict", "pr", "account"])
def test_owner_account_or_generic_comment_is_never_native_proof(tmp_path, change):
    project, pull, proof, payloads, _runs, _seen = _diwan(tmp_path)
    comment = payloads["repos/power0man/diwan/issues/comments/123"]
    if change == "generic": comment["body"] = "Looks good, merge it."
    if change == "identity": comment["body"] = comment["body"].replace("openai/codex", "human/hussain-alrabighi")
    if change == "sha": comment["body"] = comment["body"].replace(HEAD[:12], "b" * 12)
    if change == "verdict": comment["body"] = comment["body"].replace("صامد", "مرفوض")
    if change == "pr": comment["issue_url"] = "https://api.github.com/repos/power0man/diwan/issues/10"
    if change == "account": comment["user"] = {"login": "someone-else"}
    assert project.checks_with_review(pull, native_review=proof) == "failure"


def test_unresolved_inline_findings_and_changed_head_still_block(tmp_path):
    project, pull, proof, payloads, _runs, _seen = _diwan(tmp_path)
    thread = payloads["graphql"]["data"]["repository"]["pullRequest"]["reviewThreads"]
    thread["nodes"] = [{"isResolved": False}]
    assert project.checks_with_review(pull, native_review=proof) == "failure"
    thread["nodes"] = [{"isResolved": True}]
    payloads["pull"]["headRefOid"] = "b" * 40
    assert project.checks_with_review(pull, native_review=proof) == "failure"


def test_unknown_or_corrupt_trusted_source_never_falls_back_to_pr_code(tmp_path):
    project, pull, proof, payloads, _runs, _seen = _diwan(tmp_path)
    key = f"repos/power0man/diwan/contents/tools/family_review.py?ref={'f' * 40}"
    payloads[key]["sha"] = "0" * 40
    with pytest.raises(GhError, match="native_review_source_invalid"):
        project.checks_with_review(pull, native_review=proof)


@pytest.mark.parametrize("change", ["pages", "pages-items", "main", "commits", "source-type", "source-path", "source-encoding", "source-content", "files", "threads", "check-conclusion", "check-pending"])
def test_unknown_evidence_does_not_silently_become_native_acceptance(tmp_path, change):
    project, pull, proof, payloads, runs, _seen = _diwan(tmp_path)
    prefix = "repos/power0man/diwan/"
    source = payloads[prefix + f"contents/tools/family_review.py?ref={'f' * 40}"]
    if change == "pages": payloads[prefix + "pulls/9/commits"] = None
    if change == "pages-items": payloads[prefix + "pulls/9/commits"] = [[1]]
    if change == "main": payloads[prefix + "commits/main"] = {"sha": "unknown"}
    if change == "commits": payloads[prefix + "pulls/9/commits"] = [[{"commit": {"message": 1}}]]
    if change == "source-type": source["type"] = "symlink"
    if change == "source-path": source["path"] = "attacker.py"
    if change == "source-encoding": source["encoding"] = "unknown"
    if change == "source-content": source["content"] = "!"
    if change == "files": payloads[prefix + "pulls/9/files"] = [[{}]]
    if change == "threads": payloads["graphql"] = {"errors": [{"message": "unavailable"}]}
    if change == "check-conclusion": runs[2]["conclusion"] = "unknown"
    if change == "check-pending": runs[2]["status"], runs[2]["conclusion"] = "in_progress", None
    try:
        result = project.checks_with_review(pull, native_review=proof)
    except GhError as error:
        if change == "main": assert error.code == "native_review_main_unproven"
        if change == "commits": assert error.code == "native_review_commits_invalid"
        if change == "source-content": assert error.code == "native_review_source_invalid"
        result = "unavailable"
    assert result == "pending" if change == "check-pending" else result != "success"


def test_green_bot_attestation_still_requires_native_proof_and_resolved_findings(tmp_path):
    project, pull, proof, payloads, runs, _seen = _diwan(tmp_path)
    runs[-1]["conclusion"] = "success"
    payloads["repos/power0man/diwan/pulls/9/reviews"] = [[{"user": {"login": "chatgpt-codex-connector[bot]"},
        "commit_id": HEAD, "state": "COMMENTED", "submitted_at": NOW}]]
    assert project.checks_with_review(pull, native_review=proof) == "success"
    threads = payloads["graphql"]["data"]["repository"]["pullRequest"]["reviewThreads"]
    threads["nodes"] = [{"isResolved": False}]
    assert project.checks_with_review(pull, native_review=proof) == "failure"


@pytest.mark.parametrize("changes", [{"schema_version": 2}, {"head": "b" * 40}, {"code": "unknown"},
                                    {"status": "passed"}, {"author_families": [{}]}])
def test_unknown_evaluator_schema_or_verdict_is_never_a_missing_bot_exception(tmp_path, changes):
    project, pull, proof, _payloads, _runs, _seen = _diwan(tmp_path)
    runner = project.runner
    result = {"schema_version": 1, "head": HEAD, "status": "failed", "code": "no_review_from_another_family",
              "author_families": ["anthropic"], "counted": [], "blocking": [], "same_family": [], "formal_blocking": []} | changes
    def corrupt_evaluator(argv, **kwargs):
        return Done(result) if argv[0] != "gh" else runner(argv, **kwargs)
    project.runner = corrupt_evaluator
    try:
        status = project.checks_with_review(pull, native_review=proof)
    except GhError:
        status = "unavailable"
    assert status != "success"


@pytest.mark.parametrize("change", ["job-head", "job-run", "job-check", "checkout-failure", "job-log", "job-code", "job-report-head"])
def test_missing_bot_snapshot_does_not_excuse_a_different_actual_job_failure(tmp_path, change):
    project, pull, proof, payloads, _runs, _seen = _diwan(tmp_path)
    job = payloads["repos/power0man/diwan/actions/jobs/22"]
    if change == "job-head": job["head_sha"] = "b" * 40
    if change == "job-run": job["run_id"] = 12
    if change == "job-check": job["check_run_url"] = "https://api.github.com/repos/power0man/diwan/check-runs/other"
    if change == "checkout-failure": job["steps"][0]["conclusion"] = "failure"
    if change == "job-log": payloads["joblog"] = "network unavailable; no review report"
    if change == "job-code": payloads["joblog"] = payloads["joblog"].replace("no_review_from_another_family", "reviews_unreachable")
    if change == "job-report-head": payloads["joblog"] = payloads["joblog"].replace(HEAD, "b" * 40)
    assert project.checks_with_review(pull, native_review=proof) == "failure"


def test_both_declared_family_workflow_events_preserve_the_native_control(tmp_path):
    project, pull, proof, payloads, _runs, _seen = _diwan(tmp_path)
    for event in ("pull_request", "pull_request_review"):
        payloads["repos/power0man/diwan/actions/runs/11"]["event"] = event
        assert project.checks_with_review(pull, native_review=proof) == "success"


def test_same_family_advisory_presence_does_not_count_or_cancel_independent_native_pass(tmp_path):
    project, pull, proof, payloads, _runs, _seen = _diwan(tmp_path)
    payloads["repos/power0man/diwan/pulls/9/reviews"] = [[{
        "user": {"login": "claude[bot]"}, "commit_id": HEAD, "state": "COMMENTED", "submitted_at": NOW}]]
    assert project.checks_with_review(pull, native_review=proof) == "success"
    payloads["joblog"] = payloads["joblog"].replace('"no_review_from_another_family"', '"reviewed_only_by_the_author_family"').replace(
        '"same_family": []', '"same_family": ["claude[bot]"]')
    assert project.checks_with_review(pull, native_review=proof) == "success"
    assert project.checks_with_review(pull) == "failure"


def test_original_native_comment_and_formal_reviews_are_rechecked_after_source_fetch(tmp_path):
    project, pull, proof, payloads, _runs, _seen = _diwan(tmp_path)
    runner = project.runner
    def change_comment(argv, **kwargs):
        if "repos/power0man/diwan/commits/main" in argv:
            payloads["repos/power0man/diwan/issues/comments/123"]["body"] = "withdrawn"
        return runner(argv, **kwargs)
    project.runner = change_comment
    assert project.checks_with_review(pull, native_review=proof) == "failure"
    payloads["repos/power0man/diwan/issues/comments/123"]["body"] = f"مراجعةُ codex (openai/codex) على `{HEAD[:12]}`: الحكم: صامد"
    def late_formal_rejection(argv, **kwargs):
        result = runner(argv, **kwargs)
        if argv[0] != "gh":
            payloads["repos/power0man/diwan/pulls/9/reviews"] = [[{
                "user": {"login": "owner-or-other-reviewer"}, "commit_id": HEAD,
                "state": "CHANGES_REQUESTED", "submitted_at": NOW}]]
        return result
    project.runner = late_formal_rejection
    assert project.checks_with_review(pull, native_review=proof) == "failure"


def test_uncalibrated_actual_new_model_pass_during_ci_cannot_be_accepted(tmp_path):
    dispatcher, project, ledger, _ = _dispatch(tmp_path)
    dispatcher.validate(41)
    project.pulls[9] = replace(project.pulls[9], state="merged", merge_sha="e" * 40)
    record_calibration(dispatcher.home, "codex", caught=1, of=2, false_alarms=0, model="fixture-model", clock=lambda: NOW)
    reviewer = Reviewer(project, {}, ledger, tmp_path, home=dispatcher.home, clock=lambda: NOW)
    def checking(pull, *, native_review=None):
        assert native_review is not None
        assert reviewer._record(pull, FakeAdapter(name="codex", family="openai", model="new-fixture-model"),
                                HEAD, "newer-comment", "pass", execution_id="e" * 32) == "review_uncalibrated"
        return "success"
    project.checks_with_review = checking
    with pytest.raises(Refusal, match="native_review_changed_at_acceptance"):
        dispatcher.accept(41)
    assert ledger.main_state(41)["state"] == "verified" and ledger.last(41)["verdict"] == "pass"


def test_acceptance_compares_the_full_original_record_under_the_ledger_lock(tmp_path):
    dispatcher, project, ledger, evidence = _dispatch(tmp_path)
    dispatcher.validate(41)
    project.pulls[9] = replace(project.pulls[9], state="merged", merge_sha="e" * 40)
    append = ledger.append
    def append_racing(issue, state, **fields):
        if state == "accepted":
            append(issue, "reviewed_awaiting_validation", **(evidence | {"review_execution_id": "f" * 32}))
        return append(issue, state, **fields)
    ledger.append = append_racing
    with pytest.raises(TransitionError, match="review_changed_before_acceptance"):
        dispatcher.accept(41)
    assert ledger.main_state(41)["state"] == "verified"
    ledger.append = append
    # A fresh unchanged snapshot has a successful control; the newer valid pass is preserved.
    assert dispatcher.accept(41)["status"] == "accepted"


def test_unknown_formal_review_or_late_unresolved_thread_still_blocks(tmp_path):
    project, pull, proof, payloads, _runs, _seen = _diwan(tmp_path)
    payloads["repos/power0man/diwan/pulls/9/reviews"] = [[{
        "user": {"login": "unlisted-reviewer"}, "commit_id": HEAD, "state": "unknown", "submitted_at": NOW}]]
    with pytest.raises(GhError, match="native_review_evaluation_failed"):
        project.checks_with_review(pull, native_review=proof)
    payloads["repos/power0man/diwan/pulls/9/reviews"] = [[]]
    runner = project.runner
    def late_thread(argv, **kwargs):
        result = runner(argv, **kwargs)
        if argv[0] != "gh":
            payloads["graphql"]["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"] = [{"isResolved": False}]
        return result
    project.runner = late_thread
    assert project.checks_with_review(pull, native_review=proof) == "failure"


def test_a_late_unknown_formal_review_never_becomes_success(tmp_path):
    project, pull, proof, payloads, _runs, _seen = _diwan(tmp_path)
    assert project.checks_with_review(pull, native_review=proof) == "success"
    runner = project.runner
    def late_unknown(argv, **kwargs):
        result = runner(argv, **kwargs)
        if argv[0] != "gh":
            payloads["repos/power0man/diwan/pulls/9/reviews"] = [[{
                "user": {"login": "unlisted-reviewer"}, "commit_id": HEAD, "state": "unknown", "submitted_at": NOW}]]
        return result
    project.runner = late_unknown
    assert project.checks_with_review(pull, native_review=proof) == "failure"


def test_changed_ci_on_the_same_head_during_source_fetch_requires_new_validation(tmp_path):
    for status, conclusion in (("completed", "failure"), ("in_progress", None)):
        project, pull, proof, _payloads, runs, _seen = _diwan(tmp_path)
        assert project.checks_with_review(pull, native_review=proof) == "success"
        runner = project.runner
        def changed_checks(argv, **kwargs):
            result = runner(argv, **kwargs)
            if argv[0] != "gh":
                runs[0].update(status=status, conclusion=conclusion)
            return result
        project.runner = changed_checks
        assert project.checks_with_review(pull, native_review=proof) == "failure"
