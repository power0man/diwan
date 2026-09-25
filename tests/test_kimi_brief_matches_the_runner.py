"""حارسٌ لوثيقة التكليف: كلُّ مثالٍ فيها يجتاز المُشغِّلَ الحيّ.

قلتُ في التكليف إنّ العقدَ «مُشتقٌّ من المُشغِّل حرفيًّا» ثم سلّمتُ مثالًا
يردُّه المُشغِّل (حقلٌ ناقص، ومنعٌ نثريّ). فالدعوى كانت أقوى من دليلها.
وهذا الاختبارُ هو الدليل: إن انزاح عقدُ المُشغِّل أو مثالُ الوثيقة سقط.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.canonical import PayloadRejected
from evaluation.agentic_runner import validate_agentic_suite
from evaluation.capabilities import validate_suite

BRIEF = Path(__file__).resolve().parent.parent / "docs" / "KIMI-BENCHMARK-BRIEF.md"


def _json_blocks() -> list[dict]:
    text = BRIEF.read_text(encoding="utf-8")
    out = []
    for block in re.findall(r"```json\n(.*?)```", text, re.S):
        out.append(json.loads(block))   # مثالٌ غيرُ صالحٍ نحويًّا يسقط هنا
    return out


def test_the_brief_still_exists_and_carries_examples():
    blocks = _json_blocks()
    assert blocks, "وثيقةُ التكليف بلا مثالٍ = عقدٌ موصوفٌ لا مُبرهَن"


def test_every_case_suite_example_is_accepted_by_the_live_validator():
    seen = 0
    for block in _json_blocks():
        # الملفُّ الجانبيّ (§٧) فيه cases أيضًا وليس بنكَ حالات: يُميَّز
        # بأنّ بنكَ الحالات وحدَه يحمل split وschema_version.
        if not ("split" in block and "schema_version" in block):
            continue
        validate_suite(block)
        seen += 1
    assert seen >= 1, "لا مثالَ حالاتٍ في الوثيقة"


def test_every_agentic_example_is_accepted_by_the_live_validator():
    seen = 0
    for block in _json_blocks():
        if block.get("kind") == "agentic_tasks":
            validate_agentic_suite(block)
            seen += 1
    assert seen >= 1, "لا مثالَ وكيليًّا في الوثيقة"


def test_the_prose_forbidden_rule_the_brief_once_taught_is_refused():
    """يُثبت أن الحارسَ يعمل، فلا يعود المثالُ إلى النثر صامتًا."""
    for block in _json_blocks():
        if block.get("kind") != "agentic_tasks":
            continue
        bad = json.loads(json.dumps(block))
        bad["tasks"][0]["forbidden"] = ["لا تعدّل الاختبار"]
        with pytest.raises(PayloadRejected) as err:
            validate_agentic_suite(bad)
        assert "forbidden_rule_unmatchable" in str(err.value)
        return
    pytest.fail("لا مثالَ وكيليًّا لفحصه")
