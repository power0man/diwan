"""وثيقةُ حالةٍ واحدة تحيل إليها البقية، والمنسوخُ موسومٌ في موضعه (ك٢٥).

كانت الوثائقُ الحاكمة تتناقض في ١٤ موضعًا: «العمل الحالي» بثلاث روايات، وقراراتٌ نُسخت ولم
تُوسم، ومعماريةٌ تصف شجرةً لم تعد، وبرومبتُ تسليمٍ يناقض رأسه، وصفوفٌ «منجزة» بلا بصمة.
هذه الحرّاس تُبقي الإحالاتِ والوسومَ قائمة، وتفرض قاعدةَ §٣: المنجزُ يحمل بصمةَ إيداعه.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "docs/STATUS.md"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_the_status_document_exists_and_names_what_governs():
    text = STATUS.read_text(encoding="utf-8")
    for anchor in ("AGENTS.md", "docs/VISION.md", "docs/REBUILD-2026-09-23.md", "docs/DECISIONS.md",
                   "docs/EVALUATION-20260925.md", "ق٤٩", "§٣"):
        assert anchor in text, anchor


@pytest.mark.parametrize("path", ["README.md", "docs/ROADMAP.md", "docs/ARCHITECTURE.md", "docs/HANDOFF-PROMPT.md"])
def test_each_contradicting_document_points_to_the_status_document(path):
    assert "STATUS.md" in read(path), path


@pytest.mark.parametrize("decision", ["ق٤٣", "ق٤٦", "ق٤٧"])
def test_superseded_decisions_are_marked_in_place_not_deleted(decision):
    text = read("docs/DECISIONS.md")
    heading = text.index(f"## {decision} —")
    section = text[heading:heading + 1500]
    assert "وسمُ النسخ" in section, decision
    assert "النصُّ محفوظ" in section


def test_every_done_task_row_carries_a_commit_hash():
    """قاعدةُ §٣: «حين تنتهي، اكتب منجزة وبصمة الإيداع الذي أنجزها»."""
    rows = [line for line in read("AGENTS.md").split("\n") if line.startswith("| ك") or line.startswith("| ج") or line.startswith("| غ") or line.startswith("| ع")]
    done = [line for line in rows if "| منجزة" in line]
    assert done, "لا صفوفَ منجزة؟"
    # ما سبق الفتحَ موسومٌ diwan-private@ (ك٣٠)، وما بعده بصمةٌ في هذا المستودع
    missing = [line.split("|")[1].strip() for line in done if not re.search(r"\| منجزة: (?:diwan-private@)?[0-9a-f]{7,40}", line)]
    assert missing == [], missing
