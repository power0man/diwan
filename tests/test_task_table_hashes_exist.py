"""بصمةُ كلِّ مهمّةٍ «منجزة» في `AGENTS.md` §٣ إيداعٌ حقيقي على HEAD (ك٣٠).

كان الجدولُ يعتمد على أمانة العملاء (§٩): بصمةٌ مكتوبة لا يتحقّق أحدٌ أنها موجودة. الآن
كلُّ صفٍّ «منجزة» يحمل إمّا بصمةَ إيداعٍ في هذا المستودع تصل إليها HEAD، وإمّا بصمةً
سبقت الفتحَ موسومةً `diwan-private@` (تعيش في المستودع الخاص فلا تُفحص هنا). وكلُّ
«جارية» تحمل معرّفَ عميلٍ مسجَّل. ما لا يُفحص، مُعلَنًا: أن ما وُصف في الصفّ أُنجز فعلًا.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

DONE = re.compile(r"منجزة:\s*(diwan-private@)?([0-9a-f]{7,40})\b")
RUNNING = re.compile(r"جارية:\s*([a-z0-9][a-z0-9.-]*/[a-z0-9][a-z0-9._-]*)")


def task_rows() -> list[tuple[str, str]]:
    """صفوفُ جدول §٣: (الرقم، خليةُ الحالة)."""
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    section = text.split("## ٣ — المهام", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        if not line.startswith("| ") or line.startswith("| رقم") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if len(cells) >= 5:
            rows.append((cells[0], cells[-1]))
    return rows


def _git_ok(*argv: str) -> bool:
    return subprocess.run(["git", "-C", str(ROOT), *argv], capture_output=True).returncode == 0


def test_the_table_is_parsed_and_not_empty():
    rows = task_rows()
    assert len(rows) >= 30
    assert any(status.startswith("منجزة") for _, status in rows)


def test_every_done_task_names_a_commit_reachable_from_head_or_a_private_era_hash():
    unresolved = []
    for number, status in task_rows():
        if not status.startswith("منجزة"):
            continue
        match = DONE.search(status)
        assert match, f"{number}: خليةُ «منجزة» بلا بصمة"
        if match.group(1):
            continue                                   # ما قبل الفتح: في diwan-private
        sha = match.group(2)
        if not (_git_ok("cat-file", "-e", f"{sha}^{{commit}}") and _git_ok("merge-base", "--is-ancestor", sha, "HEAD")):
            unresolved.append((number, sha))
    assert not unresolved, f"بصماتٌ لا تصل إليها HEAD: {unresolved}"


def test_every_running_task_names_a_registered_agent():
    agents = json.loads((ROOT / "registry" / "agents.json").read_text(encoding="utf-8"))["agents"]
    for number, status in task_rows():
        if status.startswith("جارية"):
            match = RUNNING.search(status)
            assert match and match.group(1) in agents, f"{number}: «جارية» بلا عميلٍ مسجَّل"
