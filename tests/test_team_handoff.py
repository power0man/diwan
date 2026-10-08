"""Synthetic finished-worker artifacts and real local Git; no model or service calls."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from team.adapters.codex import CodexAdapter
from team.dispatch import Dispatcher, Refusal, build_parser
from team.handoff import Handoff
from team.ledger import TeamLedger, TransitionError
from team.projects.base import PullRequest
from team.projects.diwan import DiwanProject
from tests.team_fakes import FakeProject, GIT_ENV, git, make_repo

CONTROLLER = "anthropic/claude-fable-5-1"
SOURCE = "openai/codex"
ROOT = Path(__file__).resolve().parents[1]


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Synthetic controller")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "fixture@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Synthetic controller")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "fixture@example.invalid")
    monkeypatch.setattr("team.handoff.shutil.which", lambda _: None)
    _, repo = make_repo(tmp_path)
    (repo / "registry").mkdir()
    (repo / "registry/agents.json").write_bytes((ROOT / "registry/agents.json").read_bytes())
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__/tracked.txt").write_text("tracked source")
    git("add", "registry/agents.json", "__pycache__/tracked.txt", cwd=repo)
    git("commit", "-qm", "fixture registry", cwd=repo)
    head = git("rev-parse", "HEAD", cwd=repo)
    wt_root = tmp_path / "wt"; wt_root.mkdir()
    wt = wt_root / "team-41-openai"
    git("worktree", "add", "-b", "team/41-openai", str(wt), cwd=repo)
    project = FakeProject()
    project.handoff_findings = DiwanProject(root=wt).handoff_findings
    ledger = TeamLedger(tmp_path / "home/dispatch.jsonl")
    adapter = CodexAdapter(binary=Path("/synthetic/not-executed"))
    d = Dispatcher(project, adapter, ledger, repo, home=tmp_path / "home", wt_root=wt_root)
    raw = d.raw_dir(41, 1); raw.mkdir(parents=True)
    brief = b"Synthetic immutable task\n"
    kept = d.home / "brief.md"; kept.write_bytes(brief)
    (wt / "docs/team/briefs").mkdir(parents=True)
    (wt / "docs/team/briefs/41.md").write_bytes(brief)
    from hashlib import sha256
    ledger.append(41, "dispatched", worker="codex", family="openai", project=project.name,
                  branch="team/41-openai", worktree=str(wt), base_sha=head,
                  repo_common_dir=git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=repo),
                  brief_path=str(kept), brief_sha256=sha256(brief).hexdigest())
    ledger.append(41, "claimed", pid=4194297, started_at="2026-10-07T10:00:00+00:00")
    ledger.append(41, "validation_failed", reason="no_commits", head_sha=head, worker_ok=True)
    for name, content in {"exit": "0", "pid": "4194297", "wrapper_pid": "4194297", "child_pid": "4194298",
                          "stdout.txt": '{"type":"thread.started","thread_id":"synthetic"}\n{"type":"turn.completed"}\n',
                          "stderr.txt": "", "codex-last-message.txt": "Source ready; Git blocked."}.items():
        (raw / name).write_text(content)
    (wt / "work.txt").write_text("captured source\n")
    return d, wt, raw


def plan(d, files=None):
    return d.handoff(41, files=files or ["work.txt"], controller_agent=CONTROLLER)


def execute(d, p, files=None, controller=CONTROLLER):
    return d.handoff(41, files=files or ["work.txt"], controller_agent=controller,
                     execute=True, expected_plan_sha256=p["plan_sha256"], expected_diff_sha256=p["diff_sha256"])


def test_plan_is_read_only_and_local_commit_records_real_controller_intervention(tmp_path, monkeypatch):
    d, wt, _ = setup(tmp_path, monkeypatch)
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()}
    p = plan(d)
    assert p["status"] == "handoff_plan" and p["files"]["work.txt"]["sha256"]
    assert before == {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()}
    out = execute(d, p)
    assert out["status"] == "controller_commit"
    head = git("rev-parse", "HEAD", cwd=wt)
    assert head == out["head_sha"] and head != p["head_sha"]
    assert git("show", "HEAD:work.txt", cwd=wt) == "captured source"
    message = git("show", "-s", "--format=%B", "HEAD", cwd=wt)
    assert f"Diwan-Agent: {CONTROLLER}" in message and f"Team-Source-Agent: {SOURCE}" in message
    assert p["plan_sha256"] in message and p["diff_sha256"] in message and "worker did not commit" in message
    assert git("status", "--porcelain", "--untracked-files=all", cwd=wt) == "?? docs/team/briefs/41.md"
    assert not d.project.pulls and git("ls-remote", "origin", "refs/heads/team/41-openai", cwd=wt) == ""
    assert d.ledger.main_state(41)["state"] == "claimed"
    states = [r["state"] for r in d.ledger.records(41)]
    assert states == ["dispatched", "claimed", "validation_failed", "controller_commit_started", "controller_commit"]
    evidence = d.ledger.last(41)
    assert evidence["files"] == p["files"] and evidence["source_identity_authenticated"] is False
    with pytest.raises(Refusal, match="handoff_attempt_ineligible"):
        execute(d, p)


@pytest.mark.parametrize("case,code", [
    ("head", "handoff_head_drift"), ("diff", "handoff_plan_drift"), ("index", "handoff_index_dirty"),
    ("unnamed", "handoff_unnamed_changes"), ("symlink", "handoff_link"), ("hardlink", "handoff_not_regular"),
    ("special", "handoff_not_regular"), ("ancestor", "handoff_link"), ("delete", "handoff_deletion_refused"),
    ("brief", "handoff_brief_mismatch"), ("branch", "handoff_branch_mismatch"),
    ("index_lock", "handoff_index_busy"), ("foreign_repo", "handoff_project_mismatch"),
    ("project", "handoff_project_mismatch"), ("taken_over", "handoff_attempt_taken_over"),
    ("unknown", "handoff_attempt_ineligible"), ("unavailable", "handoff_attempt_ineligible"),
    ("missing_exit", "handoff_exit_invalid"), ("empty_exit", "handoff_exit_invalid"),
    ("nonzero", "handoff_exit_invalid"), ("live", "handoff_worker_alive"),
    ("wrapper_live", "handoff_worker_alive"), ("unknown_pid", "handoff_absence_unproven"),
    ("missing_child", "handoff_absence_unproven"), ("missing_output", "handoff_output_unreadable"),
    ("failed_output", "handoff_worker_failed"), ("no_completion", "handoff_completion_missing"),
    ("secret", "handoff_secret_content"), ("personal", "handoff_private_content"),
    ("controller", "controller_identity_not_registered"), ("head_lock", "handoff_head_busy"),
    ("exit_link", "handoff_link"), ("exit_special", "handoff_not_regular"),
    ("malformed_output", "handoff_output_unreadable"),
    ("completed", "handoff_attempt_ineligible"),
    ("tracked_cache", "handoff_unnamed_changes"),
])
def test_refuses_changed_or_unproven_evidence(tmp_path, monkeypatch, case, code):
    d, wt, raw = setup(tmp_path, monkeypatch)
    files = ["work.txt"]
    if case in {"delete", "ancestor"}:
        if case == "delete":
            files = ["README.md"]; (wt / "work.txt").unlink()
            (wt / "README.md").write_text("changed tracked source")
        else:
            files = ["dir/work.txt"]; (wt / "dir").mkdir(); (wt / "work.txt").rename(wt / files[0])
    p = plan(d, files)
    if case == "head":
        git("commit", "--allow-empty", "-qm", "advanced", cwd=wt)
    elif case == "diff": (wt / "work.txt").write_text("later bytes\n")
    elif case == "index": git("add", "work.txt", cwd=wt)
    elif case == "unnamed": (wt / "other.txt").write_text("unselected")
    elif case == "tracked_cache": (wt / "__pycache__/tracked.txt").write_text("unselected tracked source")
    elif case == "symlink":
        (wt / "work.txt").unlink(); (wt / "work.txt").symlink_to(wt / "README.md")
    elif case == "hardlink":
        (wt / "work.txt").unlink(); os.link(wt / "README.md", wt / "work.txt")
    elif case == "special":
        (wt / "work.txt").unlink(); os.mkfifo(wt / "work.txt")
    elif case == "ancestor":
        (wt / "dir").rename(wt / "elsewhere"); (wt / "dir").symlink_to(wt / "elsewhere")
    elif case == "delete": (wt / "README.md").unlink()
    elif case == "brief": (wt / "docs/team/briefs/41.md").write_text("different task")
    elif case == "branch": git("checkout", "-qb", "foreign", cwd=wt)
    elif case == "index_lock": Path(git("rev-parse", "--path-format=absolute", "--git-path", "index.lock", cwd=wt)).touch()
    elif case == "head_lock": Path(git("rev-parse", "--path-format=absolute", "--git-path", "HEAD.lock", cwd=wt)).touch()
    elif case == "foreign_repo":
        _, other = make_repo(tmp_path / "other"); d.repo_root = other
    elif case == "project": d.project.name = "different-project"
    elif case == "taken_over": (raw / "taken_over").write_text("marker")
    elif case == "unknown": d.ledger.append(41, "outcome_unknown", reason="unknown")
    elif case == "unavailable": d.ledger.append(41, "worker_unavailable", code="quota")
    elif case == "completed":
        d.ledger.append(41, "completed", head_sha=p["head_sha"], branch=p["branch"])
        d.ledger.append(41, "validation_failed", reason="no_commits", head_sha=p["head_sha"], worker_ok=True)
    elif case == "missing_exit": (raw / "exit").unlink()
    elif case == "empty_exit": (raw / "exit").write_text("")
    elif case == "nonzero": (raw / "exit").write_text("1")
    elif case == "exit_link":
        (raw / "exit").unlink(); (raw / "exit").symlink_to(raw / "pid")
    elif case == "exit_special":
        (raw / "exit").unlink(); os.mkfifo(raw / "exit")
    elif case in {"live", "wrapper_live"}: (raw / ("child_pid" if case == "live" else "wrapper_pid")).write_text(str(os.getpid()))
    elif case == "unknown_pid": (raw / "pid").write_text("bad")
    elif case == "missing_child": (raw / "child_pid").unlink()
    elif case == "missing_output": (raw / "codex-last-message.txt").unlink()
    elif case == "failed_output":
        with (raw / "stdout.txt").open("a") as f: f.write('{"type":"turn.failed"}\n')
    elif case == "no_completion": (raw / "stdout.txt").write_text('{"type":"thread.started"}\n')
    elif case == "malformed_output": (raw / "stdout.txt").write_text('{"type":"turn.completed"}\n[]\n')
    elif case == "secret": (wt / "work.txt").write_text("ghp_" + "x" * 30)
    elif case == "personal": (wt / "work.txt").write_text("fixture" + "@gmail.com")
    with pytest.raises(Refusal) as exc:
        execute(d, p, files, controller="unregistered/x" if case == "controller" else CONTROLLER)
    assert exc.value.code == code
    assert d.ledger.last_of(41, "controller_commit") is None


@pytest.mark.parametrize("path,code", [
    ("../outside", "handoff_foreign_path"), ("/absolute", "handoff_foreign_path"),
    (".env", "handoff_private_path"), ("evaluation/sealed/x", "handoff_private_path"),
    ("docs/owner/x", "handoff_private_path"), ("corpus/x", "handoff_private_path"),
    ("docs/team/briefs/41.md", "handoff_brief_selected"),
    (".", "handoff_foreign_path"), ("signing.key", "handoff_private_path"),
])
def test_refuses_explicit_foreign_private_or_brief_paths(tmp_path, monkeypatch, path, code):
    d, _, _ = setup(tmp_path, monkeypatch)
    with pytest.raises(Refusal) as exc: plan(d, [path])
    assert exc.value.code == code


def test_requires_plan_digest_and_detects_drift_during_staging(tmp_path, monkeypatch):
    d, wt, _ = setup(tmp_path, monkeypatch)
    p = plan(d)
    with pytest.raises(Refusal, match="handoff_expected_plan_required"):
        d.handoff(41, files=["work.txt"], controller_agent=CONTROLLER, execute=True)
    with pytest.raises(Refusal, match="handoff_expected_diff_required"):
        d.handoff(41, files=["work.txt"], controller_agent=CONTROLLER, execute=True, expected_plan_sha256=p["plan_sha256"])
    original = d.runner
    def runner(argv, **kwargs):
        result = original(argv, **kwargs)
        if "write-tree" in argv: (wt / "work.txt").write_text("raced with staging")
        return result
    d.runner = runner
    with pytest.raises(Refusal, match="handoff_plan_drift"): execute(d, p)
    assert git("rev-parse", "HEAD", cwd=wt) == p["head_sha"]
    assert d.ledger.last(41)["state"] == "validation_failed"


def test_git_failure_is_named_and_interrupted_intent_cannot_repeat(tmp_path, monkeypatch):
    d, wt, _ = setup(tmp_path, monkeypatch)
    p = plan(d); original = d.runner
    def runner(argv, **kwargs):
        if "commit-tree" in argv: return subprocess.CompletedProcess(argv, 1, b"", b"failure")
        return original(argv, **kwargs)
    d.runner = runner
    with pytest.raises(Refusal, match="handoff_git_failed"): execute(d, p)
    assert d.ledger.last(41)["state"] == "controller_commit_started"
    assert git("rev-parse", "HEAD", cwd=wt) == p["head_sha"]
    with pytest.raises(Refusal, match="handoff_attempt_ineligible"): execute(d, p)
    assert d.resume(41) == {"status": "outcome_unknown", "reason": "controller_commit_incomplete", "attempt": 1}
    assert not d.project.pulls


def test_git_missing_and_tmux_unknown_fail_closed(tmp_path, monkeypatch):
    d, _, _ = setup(tmp_path, monkeypatch)
    original = d.runner
    def missing(argv, **kwargs): raise FileNotFoundError("git")
    d.runner = missing
    with pytest.raises(Refusal, match="handoff_git_failed"): plan(d)
    monkeypatch.setattr("team.handoff.shutil.which", lambda _: "/fixture/tmux")
    def denied(argv, **kwargs):
        if argv[0] == "tmux": return subprocess.CompletedProcess(argv, 1, "", "Operation not permitted")
        return original(argv, **kwargs)
    d.runner = denied
    with pytest.raises(Refusal, match="handoff_session_unknown"): plan(d)


def test_read_only_ledger_never_creates_or_writes(tmp_path):
    path = tmp_path / "ledger.jsonl"
    from core.ledger import LedgerCorrupt
    with pytest.raises(LedgerCorrupt, match="read_only_ledger_missing"): TeamLedger(path, read_only=True)
    assert not path.exists() and not path.with_suffix(".jsonl.lock").exists()
    ledger = TeamLedger(path); ledger.append(41, "refused", code="synthetic")
    before = path.stat().st_mtime_ns
    ro = TeamLedger(path, read_only=True)
    assert ro.records() == ledger.records() and path.stat().st_mtime_ns == before
    with pytest.raises(TransitionError, match="read_only_ledger"): ro.append(41, "refused", code="no")


def test_source_and_controller_families_remain_excluded_from_counted_review(tmp_path):
    p = DiwanProject(root=ROOT)
    message = f"handoff\n\nDiwan-Agent: {CONTROLLER}\nTeam-Source-Agent: {SOURCE}\n"
    pull = PullRequest(1, "head", "main", "branch", 41, commit_messages=(message,))
    families = p.author_families(pull)
    assert families == {"openai", "anthropic"} and p.review_policy(families).candidates == []
    from tools.family_review import author_families
    assert author_families([{"message": message}], p.registry) == families
    from team.projects.base import ProjectError
    from tools.family_review import ReviewError
    bad = message.replace(SOURCE, "unregistered/source")
    with pytest.raises(ProjectError, match="author_not_in_trusted_registry"):
        p.author_families(PullRequest(1, "head", "main", "branch", 41, commit_messages=(bad,)))
    with pytest.raises(ReviewError, match="author_not_in_trusted_registry"):
        author_families([{"message": bad}], p.registry)


def test_handoff_cli_is_explicit_and_keeps_resume_separate():
    args = build_parser().parse_args(["handoff", "41", "--file", "work.txt", "--controller-agent", CONTROLLER])
    assert not args.execute and args.expected_plan_sha256 is None and args.files == ["work.txt"]
    with pytest.raises(SystemExit): build_parser().parse_args(["resume", "41", "--execute"])


def test_project_policy_refuses_frozen_and_nontext_content():
    p = DiwanProject(root=ROOT)
    assert "handoff_protected_path" in p.handoff_findings({"core/run.py": b"public"}, CONTROLLER)
    assert "handoff_protected_path" in p.handoff_findings({"docs/DECISIONS.md": b"public"}, CONTROLLER)
    assert "handoff_nontext_file" in p.handoff_findings({"x": b"\xff"}, CONTROLLER)
    assert "controller_identity_not_registered" in p.handoff_findings({"x": b"public"}, "unregistered/x")
    from team.projects.base import ProjectAdapter
    assert ProjectAdapter().handoff_findings({"x": b"public"}, CONTROLLER) == ["handoff_policy_missing"]


def test_unregistered_worktree_and_empty_file_list_are_refused(tmp_path, monkeypatch):
    d, wt, _ = setup(tmp_path, monkeypatch)
    with pytest.raises(Refusal, match="handoff_paths_required"):
        d.handoff(41, files=[], controller_agent=CONTROLLER)
    import team.handoff as module
    original = module.regular_bytes
    def redirected(root, rel):
        data, metadata = original(root, rel)
        return (b"/foreign/.git\n", metadata) if rel == "gitdir" else (data, metadata)
    monkeypatch.setattr(module, "regular_bytes", redirected)
    with pytest.raises(Refusal, match="handoff_worktree_unregistered"): plan(d)


def test_cli_plan_without_ledger_refuses_without_creating_home(tmp_path, monkeypatch, capsys):
    from team.dispatch import main
    home = tmp_path / "missing-home"
    monkeypatch.setenv("DIWAN_TEAM_HOME", str(home))
    assert main(["handoff", "41", "--repo-root", str(ROOT), "--file", "work.txt",
                 "--controller-agent", CONTROLLER]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "ledger_corrupt_or_missing"
    assert not home.exists()


def test_ref_update_cannot_follow_a_racing_symbolic_branch_to_main(tmp_path, monkeypatch):
    d, wt, _ = setup(tmp_path, monkeypatch)
    p = plan(d); original = d.runner
    main = git("rev-parse", "refs/heads/main", cwd=wt)
    def runner(argv, **kwargs):
        if "update-ref" in argv:
            git("symbolic-ref", "refs/heads/team/41-openai", "refs/heads/main", cwd=wt)
        return original(argv, **kwargs)
    d.runner = runner
    out = execute(d, p)
    assert git("rev-parse", "refs/heads/main", cwd=wt) == main
    assert git("rev-parse", "refs/heads/team/41-openai", cwd=wt) == out["head_sha"] != main


def test_controller_must_be_independent_of_the_source_family(tmp_path, monkeypatch):
    d, _, _ = setup(tmp_path, monkeypatch)
    for controller in (SOURCE, "human/hussain-alrabighi"):
        with pytest.raises(Refusal) as exc:
            d.handoff(41, files=["work.txt"], controller_agent=controller)
        assert exc.value.code == "handoff_controller_not_independent"
    assert plan(d)["controller_identity_authenticated"] is False


def test_cli_refusal_keeps_its_exit_code_under_python_m(tmp_path):
    env = {**os.environ, "DIWAN_TEAM_HOME": str(tmp_path / "home"), "RORO_DISABLE": "1", "PYTHONPATH": str(ROOT)}
    out = subprocess.run([sys.executable, "-m", "team.dispatch", "handoff", "41", "--execute", "--file", "work.txt",
                          "--controller-agent", CONTROLLER, "--repo-root", str(ROOT)],
                         cwd=ROOT, env=env, capture_output=True, text=True)
    assert out.returncode == 2, out.stderr
    assert json.loads(out.stdout)["code"] == "handoff_expected_plan_required"
