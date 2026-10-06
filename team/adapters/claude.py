"""محوِّل Claude Code (2.1.x): كاتبٌ بـ`-p` ومخرجٍ JSON، ومراجعٌ للقراءة فقط في جلسةٍ جديدة.

الثنائيُّ المباشر `~/.local/bin/claude` لا وسيطُ `rorolee` على PATH. **لا `--bare`:** وثيقتُه تقول إنه يتخطّى قراءة سلسلة المفاتيح
وإن المصادقة فيه «مفتاح API فقط»، فهو غير متوافق مع دخول المالك بالاشتراك (دخان ٦ أكتوبر). العزلُ عن خطّافات المستخدم وخوادمه بـ
`--setting-sources project` (إعدادُ المستودع وحده) و`--strict-mcp-config` (لا خوادم MCP من إعداد المستخدم) و`--disable-slash-commands`،
و`--settings` يفعّل صندوقَ Bash المدمج ويرفض العملَ إن تعذّر (`failIfUnavailable`)، و`--max-budget-usd` سقفُ الكلفة
المعلَنة (تقديرٌ من جهة العميل لا فاتورة). **الحدُّ المعلَن:** `acceptEdits` حدُّ أذونٍ لا حدُّ نظام تشغيل، وأدواتُ القراءة
والكتابة تعمل خارج صندوق Bash؛ فاختبارُ العزل بملفٍّ شاهد (A3) هو ما يُحتجّ به لا هذه الرايات.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .base import Adapter, AgentSpec, ReviewResult, WorkerResult, parse_verdict, unavailable_code

SPEC = AgentSpec(
    name="claude", family="anthropic",
    capabilities={"planning": True, "coding": True, "testing": True, "review": True, "long_context": True,
                  "research": False, "summarization": True, "lint": False},
    isolation={"level": "partial", "mechanism": "seatbelt_bash_only"},
    execution={"interactive": False, "structured_output": "json", "resume": True},
    availability={"type": "subscription", "dynamic": True},
    cost={"accounting": "estimate"},
    trust={"reviewer_eligible": True},
)
SANDBOX_SETTINGS = json.dumps({"sandbox": {"enabled": True, "failIfUnavailable": True}})
# عزلٌ عن إعداد المستخدم بلا --bare: مصادرُ الإعداد المستودعُ وحده، ولا خوادم MCP من المستخدم، ولا مهارات
ISOLATION_FLAGS = ("--setting-sources", "project", "--strict-mcp-config", "--disable-slash-commands")
REVIEW_TOOLS = ("Read", "Grep", "Glob", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)")


def default_binary() -> Path:
    env = os.environ.get("DIWAN_TEAM_CLAUDE_BIN")
    if env:
        return Path(env)
    direct = Path.home() / ".local" / "bin" / "claude"
    if direct.exists():
        return direct
    found = shutil.which("claude")
    return Path(found) if found else direct


class ClaudeAdapter(Adapter):
    spec = SPEC

    def __init__(self, binary: Path | None = None):
        self.binary = Path(binary) if binary else default_binary()

    def work_argv(self, worktree: Path, budget_usd: float, out_dir: Path) -> list[str]:
        return [str(self.binary), "-p", *ISOLATION_FLAGS, "--output-format", "json", "--permission-mode", "acceptEdits",
                "--max-budget-usd", f"{budget_usd:.2f}", "--settings", SANDBOX_SETTINGS]

    def parse_work(self, returncode: int, stdout: str, stderr: str, out_dir: Path) -> WorkerResult:
        try:
            payload = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
        except (json.JSONDecodeError, IndexError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        # خطأُ الدخول يأتي بخروجٍ صفر و`is_error` (دخان ٦ أكتوبر)؛ فالتعذّرُ يُقرأ من النصّ عند الفشل المعلَن أيضًا
        failed = returncode != 0 or bool(payload.get("is_error"))
        unavailable = unavailable_code(returncode, f"{stdout}\n{stderr}") if failed else None
        return WorkerResult(ok=returncode == 0 and not payload.get("is_error") and unavailable is None,
                            text=str(payload.get("result") or ""), session_id=payload.get("session_id"),
                            cost_estimate_usd=payload.get("total_cost_usd"), returncode=returncode, unavailable=unavailable)

    def review_argv(self, worktree: Path, base_branch: str) -> list[str]:
        return [str(self.binary), "-p", *ISOLATION_FLAGS, "--output-format", "json", "--permission-mode", "plan",
                "--allowedTools", *REVIEW_TOOLS]

    def parse_review(self, returncode: int, stdout: str, stderr: str) -> ReviewResult:
        try:
            payload = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
        except (json.JSONDecodeError, IndexError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        text = str(payload.get("result") or "") if payload else stdout
        failed_review = returncode != 0 or bool(payload.get("is_error"))      # خطأُ الدخول بخروجٍ صفر تعذّرٌ لا مراجعة
        unavailable = unavailable_code(returncode, f"{stdout}\n{stderr}") if failed_review else None
        return ReviewResult(ok=not failed_review and unavailable is None, verdict=parse_verdict(text), text=text,
                            returncode=returncode, unavailable=unavailable)
