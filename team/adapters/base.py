"""عقدُ محوِّل الوكيل (ق٧٦): إطلاقٌ، وقراءةُ نتيجةٍ منظَّمة، ومراجعةٌ، وإعلانُ القدرات؛ ولا أعلامَ تجاوزٍ للموافقات أبدًا.

- `AgentSpec` يحمل منذ اليوم الأول حقولَ سجلّ الوكلاء الذي يُركَّب في المرحلة ٣ (العائلة، والقدرات، والعزل وآليته،
  ونمطُ التنفيذ ومخرجُه، والتوافر، ومحاسبةُ الكلفة، والثقة)، فلا يُعاد كتابة المحوِّلات حينها.
- `assert_no_bypass` يرفض أمرَ إطلاقٍ يحمل علمَ تجاوزٍ شامل للموافقات؛ ويُثبت بالطفرة في `tests/test_team_adapters.py`.
- بيئةُ العامل قائمةُ سماحٍ لا توريثٌ كامل، ووسيطُ `rorolee` على مسار `claude` يُعطَّل بـ`RORO_DISABLE=1`.
- **الحدُّ المعلَن:** العزلُ هو عزلُ أداة الوكيل نفسِها (Seatbelt حول Bash في Claude، وصندوقُ Codex)؛ المحوِّل لا يضيف
  حدًّا، ونسخةُ العمل ليست حدًّا أمنيًّا.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

FORBIDDEN_FLAGS: tuple[str, ...] = (
    "--dangerously-skip-permissions", "--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust",
    "--yolo", "--auto", "--approve-for-me", "--allow-all",
)
FORBIDDEN_VALUES: tuple[tuple[str, str], ...] = (("--permission-mode", "bypassPermissions"), ("--approval-mode", "yolo"),
                                                 ("-s", "danger-full-access"), ("--sandbox", "danger-full-access"))
ENV_ALLOWLIST: tuple[str, ...] = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "SHELL", "USER")
UNAVAILABLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("quota_exhausted", re.compile(r"(?i)usage limit|rate limit|quota|too many requests|\b429\b|couldn't complete this request|try again later")),
    ("auth_required", re.compile(r"(?i)not logged in|unauthorized|\b401\b|please (?:log|sign) in|authentication")),
)
VERDICT = re.compile(r"الحكم\s*[:：]\s*(صامد|يحتاج تصحيحًا|يحتاج تصحيحا|مرفوض|pass|revise|reject)", re.IGNORECASE)
VERDICT_MAP = {"صامد": "pass", "pass": "pass", "يحتاج تصحيحًا": "revise", "يحتاج تصحيحا": "revise", "revise": "revise",
               "مرفوض": "reject", "reject": "reject"}


class BypassFlagError(RuntimeError):
    pass


def assert_no_bypass(argv: list[str]) -> None:
    """يرفض أمرًا يحمل علمَ تجاوزٍ شامل للموافقات أو قيمةً تساويه."""
    for item in argv:
        if item in FORBIDDEN_FLAGS:
            raise BypassFlagError(item)
    for flag, value in FORBIDDEN_VALUES:
        for i, item in enumerate(argv[:-1]):
            if item == flag and argv[i + 1] == value:
                raise BypassFlagError(f"{flag} {value}")
        if f"{flag}={value}" in argv:
            raise BypassFlagError(f"{flag}={value}")


def worker_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {key: os.environ[key] for key in ENV_ALLOWLIST if key in os.environ}
    env["RORO_DISABLE"] = "1"
    env.update(extra or {})
    return env


def unavailable_code(returncode: int, text: str) -> str | None:
    """رمزُ تعذُّر الوكيل إن دلّ عليه خروجُه ونصُّه؛ وإلا None (الفشلُ العاديّ ليس تعذُّرًا)."""
    for code, pattern in UNAVAILABLE_PATTERNS:
        if pattern.search(text or ""):
            return code
    if returncode == 127:
        return "binary_missing"
    return None


def parse_verdict(text: str) -> str:
    """الحكمُ من **آخر** سطر حكمٍ في النص؛ فالاقتباسُ («مثال: الحكم: صامد») لا يطغى على الحكم الختامي (ملاحظة Codex على #344)."""
    matches = VERDICT.findall(text or "")
    if not matches:
        return "unknown"
    last = matches[-1]
    return VERDICT_MAP[last.lower() if last.isascii() else last]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    family: str
    capabilities: dict = field(default_factory=dict)
    isolation: dict = field(default_factory=dict)
    execution: dict = field(default_factory=dict)
    availability: dict = field(default_factory=dict)
    cost: dict = field(default_factory=dict)
    trust: dict = field(default_factory=dict)


@dataclass
class WorkerResult:
    ok: bool
    text: str = ""
    session_id: str | None = None
    cost_estimate_usd: float | None = None
    returncode: int = 0
    unavailable: str | None = None


@dataclass
class ReviewResult:
    ok: bool
    verdict: str = "unknown"          # pass | revise | reject | unknown
    text: str = ""
    returncode: int = 0
    unavailable: str | None = None


class Adapter:
    spec: AgentSpec
    binary: Path

    # — ما يعلنه كل محوِّل —
    def version_argv(self) -> list[str]:
        return [str(self.binary), "--version"]

    def work_argv(self, worktree: Path, budget_usd: float, out_dir: Path) -> list[str]:
        raise NotImplementedError

    def parse_work(self, returncode: int, stdout: str, stderr: str, out_dir: Path) -> WorkerResult:
        raise NotImplementedError

    def review_argv(self, worktree: Path, base_branch: str) -> list[str]:
        raise NotImplementedError

    def parse_review(self, returncode: int, stdout: str, stderr: str) -> ReviewResult:
        text = f"{stdout}\n{stderr}"
        unavailable = unavailable_code(returncode, text) if returncode != 0 else None
        return ReviewResult(ok=returncode == 0 and unavailable is None, verdict=parse_verdict(stdout),
                            text=stdout, returncode=returncode, unavailable=unavailable)

    # — التشغيل (يُبدَّل في الاختبارات) —
    def start(self, argv: list[str], stdin_text: str, cwd: Path, stdout_path: Path, stderr_path: Path):
        """يطلق العمليةَ ويعيد مقبضَها (له pid وwait وpoll)؛ المدخلُ عبر stdin لا عبر الأمر."""
        assert_no_bypass(argv)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        out = stdout_path.open("w", encoding="utf-8")
        err = stderr_path.open("w", encoding="utf-8")
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=out, stderr=err, cwd=str(cwd),
                                env=worker_env(), text=True)
        assert proc.stdin is not None
        proc.stdin.write(stdin_text)
        proc.stdin.close()
        return proc

    def run_review(self, argv: list[str], stdin_text: str, cwd: Path, timeout: int) -> tuple[int, str, str]:
        assert_no_bypass(argv)
        try:
            done = subprocess.run(argv, input=stdin_text, cwd=str(cwd), env=worker_env(), text=True,
                                  capture_output=True, timeout=timeout)
        except FileNotFoundError:
            return 127, "", "binary_missing"
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"
        return done.returncode, done.stdout, done.stderr
