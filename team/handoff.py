"""Bounded, controller-assisted local commit of a finished Codex no_commits attempt.

Planning is read only. Execution requires explicit paths and the complete plan digest.
No worker launch, lease change, push, PR, validation or acceptance occurs here.
Git plumbing commits captured bytes without running worker-controlled filters/hooks.
The real index is locked, a separate index is built, and the branch moves by CAS.
An interrupted intent is a manual-recovery condition, never a reason to repeat execution.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from team.dispatch import GitError, Refusal, pid_alive

SOURCE_AGENT = "openai/codex"
PRIVATE_PART = re.compile(r"sealed|private|secret|credential|owner|token|password", re.I)
PRIVATE_ROOTS = {"corpus", "sources", "glossaries", "rulings", "var", "reviews"}
SECRET = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY|(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def regular_bytes(root: Path, rel: str) -> tuple[bytes, dict]:
    """Refuse links and special files before reading, also through ancestor directories."""
    if root.resolve() != root or not root.is_dir():
        raise Refusal("handoff_link")
    path = root
    for part in PurePosixPath(rel).parts:
        path = path / part
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            raise Refusal("handoff_link")
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise Refusal("handoff_not_regular")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if (before.st_dev, before.st_ino) != (st.st_dev, st.st_ino):
            raise Refusal("handoff_file_drift")
        data = handle.read()
        after = os.fstat(handle.fileno())
    current = path.lstat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if identity(st) != identity(after) or identity(st) != identity(current):
        raise Refusal("handoff_file_drift")
    return data, {"sha256": hashlib.sha256(data).hexdigest(), "mode": "100755" if st.st_mode & 0o111 else "100644",
                  "stat": [str(value) for value in identity(st)]}


class Handoff:
    def __init__(self, dispatcher):
        self.d = dispatcher

    def git(self, wt: Path, *args: str, data: bytes | None = None, index: Path | None = None) -> bytes:
        env = os.environ.copy()
        # Ambient Git routing must not redirect this bounded operation to another repo/index.
        for key in list(env):
            if key.startswith("GIT_") and key not in {"GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
                                                       "GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"}:
                env.pop(key)
        env["GIT_OPTIONAL_LOCKS"] = "0"
        env["GIT_LITERAL_PATHSPECS"] = "1"
        if index is not None:
            env["GIT_INDEX_FILE"] = str(index)
        try:
            done = self.d.runner(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                                  "-C", str(wt), *args], capture_output=True, input=data, env=env)
        except OSError as exc:
            raise Refusal("handoff_git_failed") from exc
        if done.returncode != 0:
            raise Refusal("handoff_git_failed")
        return done.stdout

    def text(self, wt, *args, **kwargs):
        return self.git(wt, *args, **kwargs).decode("utf-8").strip()

    def _absence(self, raw: Path, claimed: dict, issue: int):
        pids = [claimed.get("pid")]
        if not (raw / "child_pid").is_file():
            raise Refusal("handoff_absence_unproven")
        for name in ("pid", "wrapper_pid", "child_pid"):
            if (raw / name).exists():
                try:
                    value, _ = regular_bytes(raw, name)
                    pids.append(int(value.strip()))
                except (OSError, ValueError) as exc:
                    raise Refusal("handoff_absence_unproven") from exc
        if any(not isinstance(p, int) or p <= 0 for p in pids):
            raise Refusal("handoff_absence_unproven")
        if any(pid_alive(p) for p in pids):
            raise Refusal("handoff_worker_alive")
        if shutil.which("tmux"):
            done = self.d.runner(["tmux", "list-sessions", "-F", "#S"], capture_output=True, text=True)
            if done.returncode != 0:
                # Only tmux's explicit no-server result proves absence; permission/socket errors do not.
                if not re.search(r"no server running|no sessions|error connecting .*No such file", done.stderr or "", re.I):
                    raise Refusal("handoff_session_unknown")
            elif any(s.startswith(f"team-{issue}-") for s in (done.stdout or "").splitlines()):
                raise Refusal("handoff_worker_alive")

    @staticmethod
    def paths(files):
        if not files or len(set(files)) != len(files):
            raise Refusal("handoff_paths_required")
        for rel in files:
            p = PurePosixPath(rel)
            if not rel or not p.parts or rel != p.as_posix() or p.is_absolute() or ".." in p.parts or any(c in rel for c in "\0\n\r\t\\"):
                raise Refusal("handoff_foreign_path")
            if (any(part.startswith(".") or PRIVATE_PART.search(part) for part in p.parts) or p.parts[0] in PRIVATE_ROOTS
                    or p.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".keystore"}):
                raise Refusal("handoff_private_path")
        return sorted(files)

    def capture(self, issue: int, files: list[str], controller_agent: str):
        if controller_agent.startswith("human/") or controller_agent.split("/", 1)[0] == SOURCE_AGENT.split("/", 1)[0]:
            raise Refusal("handoff_controller_not_independent")
        files = self.paths(files)
        ledger = self.d.ledger
        attempt = ledger.attempt_of(issue)
        dispatched = ledger.last_of(issue, "dispatched", attempt) or {}
        claimed = ledger.last_of(issue, "claimed", attempt) or {}
        main = ledger.main_state(issue) or {}
        failure = ledger.last(issue) or {}
        if (failure.get("state") != "validation_failed" or failure.get("reason") != "no_commits"
                or main.get("state") != "claimed" or main.get("attempt") != attempt
                or failure.get("attempt") != attempt or failure.get("worker_ok") is not True
                or dispatched.get("worker") != "codex" or dispatched.get("family") != "openai" or not claimed):
            raise Refusal("handoff_attempt_ineligible")
        raw = self.d.raw_dir(issue, attempt)
        if raw.resolve() != raw:
            raise Refusal("handoff_link")
        if ledger.last_of(issue, "takeover", attempt) or (raw / "taken_over").exists():
            raise Refusal("handoff_attempt_taken_over")
        wt = Path(dispatched.get("worktree", ""))
        if (not wt.is_absolute() or wt.resolve() != wt or wt != self.d.wt_root.resolve() / f"team-{issue}-openai{self.d._suffix(attempt)}"):
            raise Refusal("handoff_worktree_mismatch")
        common = self.text(wt, "rev-parse", "--path-format=absolute", "--git-common-dir")
        repo_common = self.text(self.d.repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if common != repo_common or self.text(wt, "rev-parse", "--show-toplevel") != str(wt):
            raise Refusal("handoff_project_mismatch")
        git_dir = Path(self.text(wt, "rev-parse", "--absolute-git-dir"))
        try:
            registration, _ = regular_bytes(git_dir, "gitdir")
        except OSError as exc:
            raise Refusal("handoff_worktree_unregistered") from exc
        if registration.decode().strip() != str(wt / ".git"):
            raise Refusal("handoff_worktree_unregistered")
        # Legacy phase-1 records pin the repo through Git's registered worktree/common-dir;
        # new records additionally bind the project adapter and repository root.
        if (dispatched.get("project", self.d.project.name) != self.d.project.name
                or dispatched.get("repo_common_dir", common) != common):
            raise Refusal("handoff_project_mismatch")
        branch = self.text(wt, "symbolic-ref", "--short", "HEAD")
        if branch != dispatched.get("branch") or branch != f"team/{issue}-openai{self.d._suffix(attempt)}":
            raise Refusal("handoff_branch_mismatch")
        head = self.text(wt, "rev-parse", "HEAD")
        if head != failure.get("head_sha") or head != dispatched.get("base_sha"):
            raise Refusal("handoff_head_drift")
        self._absence(raw, claimed, issue)
        try:
            exit_data, _ = regular_bytes(raw, "exit")
        except FileNotFoundError as exc:
            raise Refusal("handoff_exit_invalid") from exc
        if exit_data.strip() != b"0":
            raise Refusal("handoff_exit_invalid")
        evidence = {}
        contents = {}
        for name in ("exit", "stdout.txt", "stderr.txt", "codex-last-message.txt", "child_pid"):
            try:
                contents[name], evidence[name] = regular_bytes(raw, name)
            except (OSError, UnicodeError) as exc:
                raise Refusal("handoff_output_unreadable") from exc
        for name in ("pid", "wrapper_pid"):
            if (raw / name).exists():
                _, evidence[name] = regular_bytes(raw, name)
        if not contents["stdout.txt"].strip() or not contents["codex-last-message.txt"].strip():
            raise Refusal("handoff_output_unreadable")
        try:
            events = [json.loads(line) for line in contents["stdout.txt"].decode().splitlines() if line.strip()]
        except (ValueError, UnicodeError) as exc:
            raise Refusal("handoff_output_unreadable") from exc
        if not all(isinstance(e, dict) for e in events):
            raise Refusal("handoff_output_unreadable")
        if not any(e.get("type") == "turn.completed" for e in events):
            raise Refusal("handoff_completion_missing")
        # Use the registered adapter, not whichever worker happens to be selected on the CLI.
        adapter = self.d.adapter_for("codex")
        try:
            result = adapter.parse_work(0, contents["stdout.txt"].decode(), contents["stderr.txt"].decode(), raw)
        except (OSError, UnicodeError) as exc:
            raise Refusal("handoff_output_unreadable") from exc
        if not result.ok or result.unavailable:
            raise Refusal("handoff_worker_failed")
        brief = f"docs/team/briefs/{issue}.md"
        try:
            kept = Path(dispatched["brief_path"])
            stored, _ = regular_bytes(kept.parent, kept.name)
            local, _ = regular_bytes(wt, brief)
        except (KeyError, OSError) as exc:
            raise Refusal("handoff_brief_mismatch") from exc
        if (hashlib.sha256(stored).hexdigest() != dispatched.get("brief_sha256") or local != stored):
            raise Refusal("handoff_brief_mismatch")
        if brief in files:
            raise Refusal("handoff_brief_selected")
        if self.git(wt, "diff", "--no-ext-diff", "--no-textconv", "--cached", "--name-only", "-z", "HEAD"):
            raise Refusal("handoff_index_dirty")
        index_path = Path(self.text(wt, "rev-parse", "--path-format=absolute", "--git-path", "index"))
        if index_path.with_name("index.lock").exists() and not getattr(self, "_own_index_lock", False):
            raise Refusal("handoff_index_busy")
        head_lock = Path(self.text(wt, "rev-parse", "--path-format=absolute", "--git-path", "HEAD.lock"))
        if head_lock.exists():
            raise Refusal("handoff_head_busy")
        data, entries = {}, {}
        for rel in files:
            try:
                data[rel], entries[rel] = regular_bytes(wt, rel)
            except FileNotFoundError as exc:
                raise Refusal("handoff_deletion_refused") from exc
            tracked = self.text(wt, "ls-tree", "HEAD", "--", rel)
            if tracked and tracked.split()[0] not in {"100644", "100755"}:
                raise Refusal("handoff_link")
            if SECRET.search(data[rel]):
                raise Refusal("handoff_secret_content")
        # Separate name lists avoid rename ambiguity. Include ignored files too; only generated
        # Python caches are exempt. No hidden ignored content is added or read.
        tracked_changes = {p for p in self.git(wt, "diff", "--no-ext-diff", "--no-textconv", "--name-only", "--no-renames", "-z", "HEAD").decode().split("\0") if p}
        changed = set()
        for args in (("ls-files", "--others", "--exclude-standard", "-z"),
                     ("ls-files", "--others", "--ignored", "--exclude-standard", "-z")):
            changed.update(p for p in self.git(wt, *args).decode().split("\0") if p)
        from team.dispatch import is_build_cache
        changed = {p for p in changed if not is_build_cache(p)}
        changed.update(tracked_changes)  # A tracked file is source even when its directory looks like a cache.
        changed.discard(brief)
        if changed != set(files):
            raise Refusal("handoff_unnamed_changes")
        findings = self.d.project.handoff_findings(data, controller_agent)
        if findings:
            raise Refusal(findings[0])
        plan = {"issue": issue, "attempt": attempt, "project": self.d.project.name, "repo_common_dir": common,
                "worktree": str(wt), "branch": branch, "head_sha": head, "brief_sha256": dispatched["brief_sha256"],
                "source_agent": SOURCE_AGENT, "source_identity_authenticated": False, "controller_agent": controller_agent,
                "controller_identity_authenticated": False,
                "files": entries, "evidence": evidence, "ledger_sha256": digest(ledger.records(issue)),
                "index_sha256": hashlib.sha256(index_path.read_bytes()).hexdigest()}
        plan["diff_sha256"] = digest({"head": head, "files": entries})
        return plan, data, index_path

    def plan(self, issue, files, controller_agent):
        try:
            plan, _, _ = self.capture(issue, files, controller_agent)
        except (OSError, UnicodeError, GitError) as exc:
            raise Refusal("handoff_evidence_unreadable") from exc
        return {"status": "handoff_plan", **plan, "plan_sha256": digest(plan)}

    def execute(self, issue, files, controller_agent, expected_plan_sha256, expected_diff_sha256):
        if not re.fullmatch(r"[a-f0-9]{64}", expected_plan_sha256 or ""):
            raise Refusal("handoff_expected_plan_required")
        if not re.fullmatch(r"[a-f0-9]{64}", expected_diff_sha256 or ""):
            raise Refusal("handoff_expected_diff_required")
        # This lock also excludes cooperating takeover/dispatch ledger mutations. No lease renewal.
        with self.d.ledger._locked():
            self.d.ledger.ledger.verify_chain(strict=True)
            plan, data, index = self.capture(issue, files, controller_agent)
            if digest(plan) != expected_plan_sha256 or plan["diff_sha256"] != expected_diff_sha256:
                raise Refusal("handoff_plan_drift")
            wt = Path(plan["worktree"])
            lock_path = index.with_name("index.lock")
            try:
                lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except OSError as exc:
                raise Refusal("handoff_index_busy") from exc
            os.close(lock_fd)
            try:
                with tempfile.TemporaryDirectory(prefix="team-handoff-", dir=index.parent) as tmp:
                    isolated = Path(tmp) / "index"
                    self.git(wt, "read-tree", plan["head_sha"], index=isolated)
                    for rel in sorted(data):
                        blob = self.text(wt, "hash-object", "-w", "--stdin", data=data[rel])
                        self.git(wt, "update-index", "--add", "--cacheinfo", plan["files"][rel]["mode"], blob, rel, index=isolated)
                    tree = self.text(wt, "write-tree", index=isolated)
                    # Temporarily allow our own index lock when recapturing, without removing it.
                    again = self._recapture_locked(issue, files, controller_agent)
                    if digest(again) != expected_plan_sha256:
                        raise Refusal("handoff_plan_drift")
                    self.d.ledger._append(issue, "controller_commit_started", head_sha=plan["head_sha"],
                                          plan_sha256=expected_plan_sha256, controller_agent=controller_agent, source_agent=SOURCE_AGENT)
                    message = (f"Controller-assisted local handoff #{issue}\n\n"
                               "Captured source bytes; the worker did not commit. Source identity is self-declared, unauthenticated.\n"
                               "تسليم: إيداع محدود من المنسق لفرق العامل؛ لا تعديل دلالي ولا قبول أو مراجعة.\n\n"
                               f"Diwan-Agent: {controller_agent}\nTeam-Source-Agent: {SOURCE_AGENT}\n"
                               f"Team-Handoff-Plan: {expected_plan_sha256}\nTeam-Handoff-Diff: {plan['diff_sha256']}\n"
                               "Team-Intervention: controller-assisted-local-commit\n")
                    head = self.text(wt, "commit-tree", tree, "-p", plan["head_sha"], data=message.encode())
                    # Never follow a symbolic branch ref to main or any other branch.
                    self.git(wt, "update-ref", "--no-deref", f"refs/heads/{plan['branch']}", head, plan["head_sha"])
                    os.replace(isolated, index)
                    self.d.ledger._append(issue, "controller_commit", head_sha=head, parent_sha=plan["head_sha"],
                                          plan_sha256=expected_plan_sha256, diff_sha256=plan["diff_sha256"], files=plan["files"],
                                          controller_agent=controller_agent, source_agent=SOURCE_AGENT,
                                          source_identity_authenticated=False, controller_identity_authenticated=False,
                                          intervention="controller-assisted-local-commit")
                    return {"status": "controller_commit", "head_sha": head, "parent_sha": plan["head_sha"],
                            "plan_sha256": expected_plan_sha256, "intervention": "controller-assisted-local-commit"}
            finally:
                lock_path.unlink()

    def _recapture_locked(self, issue, files, controller_agent):
        self._own_index_lock = True
        try:
            return self.capture(issue, files, controller_agent)[0]
        finally:
            self._own_index_lock = False
