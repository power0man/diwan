"""محوِّل Codex CLI (0.160.x): كاتبٌ بـ`codex exec --json -s workspace-write`، والمراجعُ الافتراضي بـ`codex review --base`.

`codex exec` غيرُ تفاعليّ بطبيعته في هذا الإصدار (لا راية `-a`)، وأعلامُ التجاوز فيه `--approve-for-me`
و`--dangerously-bypass-approvals-and-sandbox` ممنوعةٌ بالعقد. الصندوقُ `workspace-write` (Seatbelt على macOS) يحصر الكتابةَ في
جذر العمل ويطفئ الشبكة. المخرجُ JSONL: `thread.started` يحمل معرّفَ الجلسة، و`turn.completed` الاستهلاكَ بالرموز، والرسالةُ
الأخيرة تُكتب بـ`-o`. لا رايةَ ميزانيةٍ بالدولار في `exec`، فالكلفةُ هنا رموزٌ لا دولارات (`cost_estimate_usd=None`).
**الحدُّ المعلَن:** «القراءةُ كاملةٌ دائمًا» في صندوق Codex؛ فالعاملُ يرى القرصَ كلَّه قراءةً وإن لم يكتب خارج جذره.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .base import Adapter, AgentSpec, WorkerResult, unavailable_code

SPEC = AgentSpec(
    name="codex", family="openai",
    capabilities={"planning": False, "coding": True, "testing": True, "review": True, "long_context": False,
                  "research": False, "summarization": False, "lint": True},
    isolation={"level": "strong", "mechanism": "seatbelt_workspace_write"},
    execution={"interactive": False, "structured_output": "jsonl", "resume": True},
    availability={"type": "subscription", "dynamic": True},
    cost={"accounting": "tokens_only"},
    trust={"reviewer_eligible": True},
)
LAST_MESSAGE = "codex-last-message.txt"


def default_binary() -> Path:
    env = os.environ.get("DIWAN_TEAM_CODEX_BIN")
    if env:
        return Path(env)
    found = shutil.which("codex")
    return Path(found) if found else Path.home() / ".local" / "bin" / "codex"


class CodexAdapter(Adapter):
    spec = SPEC

    def __init__(self, binary: Path | None = None):
        self.binary = Path(binary) if binary else default_binary()

    def work_argv(self, worktree: Path, budget_usd: float, out_dir: Path) -> list[str]:
        return [str(self.binary), "exec", "--json", "-s", "workspace-write", "-C", str(worktree),
                "-o", str(out_dir / LAST_MESSAGE), "-"]

    def parse_work(self, returncode: int, stdout: str, stderr: str, out_dir: Path) -> WorkerResult:
        session_id = None
        failed = False
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "thread.started":
                session_id = event.get("thread_id") or event.get("id")
            elif kind in ("turn.failed", "error"):
                failed = True
        last = out_dir / LAST_MESSAGE
        text = last.read_text(encoding="utf-8") if last.exists() else ""
        unavailable = unavailable_code(returncode, f"{stdout}\n{stderr}") if (returncode != 0 or failed) else None
        return WorkerResult(ok=returncode == 0 and not failed and unavailable is None, text=text, session_id=session_id,
                            cost_estimate_usd=None, returncode=returncode, unavailable=unavailable)

    def review_argv(self, worktree: Path, base_branch: str) -> list[str]:
        return [str(self.binary), "review", "--base", base_branch, "-"]
