"""حارسُ `docs/TASKS.jsonl` وتجميدُ جدول §٣ (ك٤١، ق٦١).

السجلُّ ما تُلحقه `tools/issue_ledger.py` لكل مسألةٍ ثبت إنجازُها. وهذا الحارسُ يعيد
فحصَه من النسخة وحدها بلا شبكة:
- كلُّ سطرٍ بحقوله المعلنة، ولا تتكرّر مسألة.
- كلُّ بصمة دمجٍ إيداعٌ تصل إليه HEAD.
- كلُّ عميلٍ مذكور مسجَّل.

وجدولُ `AGENTS.md` §٣ صار تاريخًا: لا يُضاف إليه صفّ. فالمهامُّ الجديدة مسائل، وإثباتُها هنا.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

from issue_ledger import ENTRY_KEYS, LEDGER, TASK_TITLE  # noqa: E402
from test_task_table_hashes_exist import task_rows  # noqa: E402

LINES = [line for line in (ROOT / LEDGER).read_text(encoding="utf-8").splitlines() if line.strip()]
ENTRIES = [json.loads(line) for line in LINES]
REGISTERED = set(json.loads((ROOT / "registry" / "agents.json").read_text(encoding="utf-8"))["agents"])
# جدولُ §٣ عند تجميده في ك٤١. صفٌّ يُضاف بعده مهمّةٌ خارج المتتبِّع.
FROZEN_TASKS = (
    "ك١", "ك٢", "ك٣", "ك٤", "ك٨", "ج١", "ج٢", "غ١", "غ٢", "ك٥", "ك٦", "ج٣", "ك٧", "ك٩", "ك١٠",
    "ك١١", "ك١٢", "ك١٣", "ك١٤", "ك١٥", "ك١٦", "ع١", "ع٢", "ك٢٦", "ك١٧", "ك١٨", "ك١٩", "ك٢٠",
    "ك٢١", "ك٢٢", "ك٢٣", "ك٢٤", "ك٢٥", "ج٤", "ك٢٧", "ك٢٨", "ك٢٩", "ك٣١", "ك٣٢", "ك٣٣", "ك٣٤",
    "ك٣٠", "ك٣٥", "ك٣٦", "ك٣٧",
)


def _git_ok(*argv: str) -> bool:
    return subprocess.run(["git", "-C", str(ROOT), *argv], capture_output=True).returncode == 0


def test_the_ledger_is_not_empty_and_every_line_has_its_declared_fields():
    assert ENTRIES, "السجلُّ فارغ"
    for entry in ENTRIES:
        assert tuple(entry) == ENTRY_KEYS, entry
        assert entry["schema_version"] == 1
        assert TASK_TITLE.match(f"[{entry['task']}]"), entry
        assert isinstance(entry["issue"], int) and isinstance(entry["pull"], int)
        assert re.fullmatch(r"[0-9a-f]{40}", entry["merge"]), entry


def test_no_issue_is_recorded_twice():
    issues = [entry["issue"] for entry in ENTRIES]
    assert len(issues) == len(set(issues))


def test_every_merge_is_a_commit_reachable_from_head():
    unresolved = [(e["issue"], e["merge"]) for e in ENTRIES
                  if not (_git_ok("cat-file", "-e", f"{e['merge']}^{{commit}}")
                          and _git_ok("merge-base", "--is-ancestor", e["merge"], "HEAD"))]
    assert not unresolved, f"بصماتُ دمجٍ لا تصل إليها HEAD: {unresolved}"


def test_every_entry_names_registered_agents():
    assert all(entry["agents"] for entry in ENTRIES), "سطرٌ بلا عميل"
    unknown = sorted({a for e in ENTRIES for a in e["agents"]} - REGISTERED)
    assert not unknown, f"عملاءُ غير مسجَّلين: {unknown}"


def test_the_task_table_is_frozen():
    assert tuple(number for number, _ in task_rows()) == FROZEN_TASKS
