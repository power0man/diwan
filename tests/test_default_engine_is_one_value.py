"""المحرّكُ الافتراضي قيمةٌ واحدة في موضعٍ واحد، والوثائقُ تتبعها (ق٥٤).

`providers/ollama.py::DEFAULT_MODEL` هو المصدر؛ وفحصُ الدخان والواجهةُ يستوردانه، وREADME
و`AGENTS.md` يسمّيانه بنصّه. فإن بُدّل المحرّك بقاعدة ق٥٩ في موضعه سقط هذا الحارس حتى تتبعه
الوثائق، ولا يعيش README بمحرّكٍ غير الذي يشغّله الكود. وقاعدةُ التحكيم (§٤) تتبع عائلتَه.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import launch_check  # noqa: E402
from providers.ollama import DEFAULT_MODEL, OllamaProvider  # noqa: E402


def test_the_provider_and_the_smoke_check_share_one_default():
    assert OllamaProvider().model == DEFAULT_MODEL
    assert launch_check.DEFAULT_ENGINE == DEFAULT_MODEL


def test_the_readme_quickstart_pulls_the_default_engine():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert re.search(rf"^ollama pull {re.escape(DEFAULT_MODEL)}\b", readme, re.M), DEFAULT_MODEL


def test_the_agents_rules_name_the_default_engine():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert f"المحرّك اليوم `{DEFAULT_MODEL}`" in agents, "§٤: قاعدةُ التحكيم تسمّي المحرّك"
    assert f"المحرّك الافتراضي `{DEFAULT_MODEL}`" in agents, "§٨: خريطةُ المستودع تسمّي المحرّك"


def test_the_ui_prefers_the_default_engine_first():
    source = (ROOT / "tools" / "serve_ui.py").read_text(encoding="utf-8")
    assert "for pref in (DEFAULT_MODEL," in source


def test_the_reviewer_rule_follows_the_engine_family():
    from evaluation.external_review import ENGINE_FAMILY, reviewer_family
    assert reviewer_family(DEFAULT_MODEL) == ENGINE_FAMILY
