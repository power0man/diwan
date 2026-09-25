"""حارسُ ملفّ الأدوار: كلُّ ذكاءٍ يعمل على ديوان يجد دورَه، ويجد أن ديوان عام.

الانحصارُ في السياسات تكرّر لأن كلَّ عميلٍ كان يستنتج الاتجاهَ من المتن الموجود.
فصار `AGENTS.md` هو ما يُحمَّل أولًا. وهذا الحارسُ يمنع ثلاثةَ انزلاقات صامتة:
أن تُحذف القاعدة، أو ينفصل ملفّا Claude وGemini عن المصدر، أو يُسجَّل عميلٌ بلا دور.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"
RULE = "ديوان عام؛ والسياسات عقدة"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_instructions_file_states_that_diwan_is_general():
    assert RULE in _text(AGENTS)


def test_claude_and_gemini_load_the_single_source():
    for name in ("CLAUDE.md", "GEMINI.md"):
        first = _text(ROOT / name).lstrip().splitlines()[0]
        assert first == "@AGENTS.md", f"{name} لا يستورد AGENTS.md في أوّل سطر"


def test_every_registered_agent_has_a_role():
    """تسجيلُ عميلٍ في السجلّ بلا سطرٍ في جدول الأدوار يُسقط هذا الاختبار."""
    registry = json.loads(_text(ROOT / "registry" / "agents.json"))
    text = _text(AGENTS)
    missing = [agent for agent in registry["agents"] if f"`{agent}`" not in text]
    assert missing == [], f"عملاءُ مسجَّلون بلا دور في AGENTS.md: {missing}"


def test_the_paste_briefs_for_external_models_exist():
    """الذكاءاتُ الخارجية لا ترى المستودع، فتكليفُها يجب أن يكون جاهزًا للّصق."""
    for brief in ("docs/KIMI-BENCHMARK-BRIEF.md", "docs/REVIEWER-BRIEF.md",
                  "docs/external/KIMI-NEXT.md"):
        assert brief in _text(AGENTS)
        assert (ROOT / brief).is_file(), brief


def test_the_owners_vision_drawing_is_referenced_and_present():
    assert "docs/vision/diwan-hub.jpg" in _text(AGENTS)
    assert (ROOT / "docs" / "vision" / "diwan-hub.jpg").stat().st_size > 0


def test_the_startup_protocol_and_its_prompt_exist():
    """البرومبتُ الموحّد يوجّه إلى الملف، والملفُّ يحمل البروتوكولَ الذي يُطلب إعلانُه."""
    text = _text(AGENTS)
    assert "## ٠ — بروتوكول البدء" in text
    prompt = _text(ROOT / "docs" / "START-PROMPT.md")
    assert "AGENTS.md" in prompt and "§٠" in prompt
    assert "docs/START-PROMPT.md" in text


def test_every_task_row_has_a_known_state():
    """حالةٌ خارج المفردات المعلنة تعني أن الجدولَ لم يعد يُقرأ آليًّا."""
    states = ("مفتوحة", "جارية:", "منجزة:", "معطّلة:")
    rows = [line for line in _text(AGENTS).splitlines()
            if line.startswith("| ") and line.count("|") == 6
            and not line.startswith("| رقم") and not line.startswith("|---")]
    assert rows, "لا صفوفَ مهامّ في §٣"
    bad = [r for r in rows if not r.rstrip(" |").split("|")[-1].strip().startswith(states)]
    assert bad == [], f"صفوفٌ بحالةٍ غير معروفة: {bad}"
