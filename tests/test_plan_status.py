"""حالةُ مهامّ الخطة من إثباتها (ق٧٣ الخطوة 0.4): السجلُّ يُنجز، والأرشيفُ يُنجز، والتأجيلُ بقراره، وما سواها مفتوح."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tools.plan_status as ps  # noqa: E402


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    tasks = [{"id": "جديد-a", "title": "أ", "assignee": "claude-cloud", "phase": "م٠-أ"},
             {"id": "ك٣٠", "title": "ب", "assignee": "claude-cloud", "phase": "م١"},
             {"id": "ح٥", "title": "ج", "assignee": "owner", "phase": "م١",
              "deferred": {"by": "ق٦٨", "until": "2026-10-19", "reason": "Nitro"}},
             {"id": "جديد-d", "title": "د", "assignee": "codex", "phase": "م١"},
             {"id": "جديد-is-local-guard", "title": "هـ", "assignee": "claude-cloud", "phase": "م٠-أ"}]
    (tmp_path / ps.PLAN).write_text(json.dumps({"tasks": tasks}, ensure_ascii=False), encoding="utf-8")
    ledger = [{"task": "جديد-a", "issue": 7, "pull": 9, "merge": "abcdef0123"},
              {"task": "ك٥٦", "issue": 138, "pull": 247, "merge": "4275871200"}]
    (tmp_path / ps.LEDGER).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ledger), encoding="utf-8")
    (tmp_path / ps.ARCHIVE).write_text("| ك٣٠ | التنسيق | أيّ عميل | — | منجزة: df52962 — حارس |\n", encoding="utf-8")
    (tmp_path / ps.AGENTS).write_text(AGENTS_TABLE.format(status="منجزة: 1234567"), encoding="utf-8")
    return tmp_path


AGENTS_TABLE = """## ٣ — المهام (العملاء يحدّثون هذا الجدول بأنفسهم)

| رقم | المهمة | المسؤول | شرط البدء | الحالة |
|---|---|---|---|---|
| ك٦ | أتمتة | Claude | — | مفتوحة |
| ك٥٦ | المحليّة | Claude | — | {status} |

## ٤ — قواعد
"""


def test_status_comes_from_the_ledger_the_archive_and_the_plan_deferral(tmp_path):
    rows = {r["id"]: r for r in ps.derive(_repo(tmp_path))}
    assert (rows["جديد-a"]["status"], rows["جديد-a"]["proof"]) == ("منجزة", "#7 ← #9 (`abcdef0`)")
    assert (rows["ك٣٠"]["status"], rows["ك٣٠"]["proof"]) == ("منجزة", "الأرشيف (`df52962`)")
    assert (rows["ح٥"]["status"], rows["ح٥"]["proof"]) == ("مؤجَّلة", "ق٦٨ حتى 2026-10-19")
    assert rows["جديد-d"]["status"] == "مفتوحة"
    alias = rows["جديد-is-local-guard"]                       # سُلِّمت بمعرّفٍ آخر تسمّيه STATUS
    assert (alias["status"], alias["proof"]) == ("منجزة", "ك٥٦ #138 ← #247 (`4275871`)")
    text = ps.render(list(rows.values()))
    assert "**3 منجزة**، منها 2 بطلبٍ مدموج" in text and "و1 ببصمة إيداعٍ في الأرشيف وحدها" in text
    assert "| م١ | 1 | 1 | 1 |" in text


def test_check_fails_when_the_generated_file_is_stale_and_write_repairs_it(tmp_path, capsys):
    root = _repo(tmp_path)
    assert ps.main(["--check", "--root", str(root)]) == 1
    assert ps.main(["--write", "--root", str(root)]) == 0
    assert ps.main(["--check", "--root", str(root)]) == 0


def test_the_committed_plan_status_is_current():
    """الملفُّ المودَع مطابقٌ لما تولّده الأداة، فلا يُلحق سجلُّ الإثبات سطرًا ويبقى الملفُّ قديمًا."""
    assert (ROOT / ps.OUT).read_text(encoding="utf-8") == ps.render(ps.derive(ROOT))


def test_an_open_agents_row_that_the_ledger_proves_fails_the_check(tmp_path, capsys):
    """كانت ع٢ مثبتةً في السجلّ ومفتوحةً في AGENTS.md أسبوعًا؛ فالفحصُ يحمرّ ما دام التعارض (ملاحظة Codex على #312)."""
    root = _repo(tmp_path)
    assert ps.conflicts(root) == []
    (root / ps.AGENTS).write_text(AGENTS_TABLE.format(status="مفتوحة"), encoding="utf-8")
    assert ps.conflicts(root) == ["ك٥٦"]
    assert ps.main(["--write", "--root", str(root)]) == 0
    assert "تعارضٌ مع `AGENTS.md` §٣" in (root / ps.OUT).read_text(encoding="utf-8")
    assert ps.main(["--check", "--root", str(root)]) == 1
