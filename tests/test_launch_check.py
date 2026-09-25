"""فحصُ الدخان يسمّي ما يعمل وما تعذّر وما عطب، ولا يخلط بينها (ك٢٨).

بلا محرّكٍ حيّ هنا: يُحاكى خادمُ Ollama بدالّةِ قراءةٍ مزيّفة، وتُشغَّل الجولةُ الوكيلة بالمزوّد
المكتوب سلفًا فتُثبت الحلقةَ والأدوات، وتُقرأ عقدةُ السياسات على حال هذه النسخة. والحدُّ معلَن:
الجولةُ الحيّة بالمحرّك لا تُختبر هنا.
"""
from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import launch_check as lc  # noqa: E402


def _tags_with(*names):
    return lambda base_url: {"models": [{"name": n, "digest": "d" * 12} for n in names]}


def _down(base_url):
    raise urllib.error.URLError("connection refused")


def test_runtime_is_ready_here():
    step = lc.check_runtime(ROOT)
    assert step.status == "ok" and step.code.startswith("runtime_ready_")


def test_engine_states_are_named():
    assert lc.check_engine("qwen3:14b", "http://127.0.0.1:1", probe=_down).code == "engine_unreachable"
    missing = lc.check_engine("qwen3:14b", "http://x", probe=_tags_with("llama3:8b"))
    assert missing.status == "unavailable" and missing.code == "model_missing" and "ollama pull qwen3:14b" in missing.detail
    ready = lc.check_engine("qwen3:14b", "http://x", probe=_tags_with("qwen3:14b", "llama3:8b"))
    assert ready.status == "ok" and ready.code == "engine_ready"


def test_the_mechanism_turn_reads_the_file_through_the_governed_loop():
    step = lc.check_agent_turn("qwen3:14b", "http://x", live=False)
    assert step.status == "ok" and step.code == "mechanism_only", step
    assert lc.NOTE_TEXT in step.detail


def test_morphology_names_what_is_missing_and_is_never_ready_without_the_database():
    """ق٥٥: CAMeL لازمٌ للإطلاق — غيابُه أو غيابُ قاعدته تعذّرٌ معلن يسمّي أمرَه."""
    missing = lc.check_morphology(installed=lambda: False)
    assert missing.status == "unavailable" and missing.code == "camel_missing"
    assert lc.CAMEL_INSTALL in missing.detail and lc.CAMEL_DB_COMMAND in missing.detail
    no_db = lc.check_morphology(installed=lambda: True, analyzer=None)
    assert no_db.status == "unavailable" and no_db.code == "camel_db_missing"
    assert lc.CAMEL_DB_COMMAND in no_db.detail
    ready = lc.check_morphology(installed=lambda: True, analyzer=object())
    assert ready.status == "ok" and ready.code == "camel_ready"


def test_morphology_here_reports_the_real_state_of_this_machine():
    from core.linguistics.roots import camel_available
    step = lc.check_morphology()
    assert step.code in ("camel_missing", "camel_db_missing", "camel_ready"), step
    assert (step.status == "ok") == camel_available()


def test_a_model_that_answers_without_the_tool_is_a_failure(monkeypatch):
    class Silent(lc._Mechanism):
        def complete(self, request):
            from core.contracts import Response, Usage
            return Response("جوابٌ بلا قراءة", Usage(1, 1), "complete", 0, provider=self.name,
                            model_version="v1", tool_calls=())
    monkeypatch.setattr(lc, "_Mechanism", Silent)
    step = lc.check_agent_turn("qwen3:14b", "http://x", live=False)
    assert step.status == "failed" and step.code == "tool_not_used"


def test_policies_reflect_whether_the_corpus_is_placed():
    step = lc.check_policies(ROOT)
    if (ROOT / "corpus" / "maritime" / "_catalog.jsonl").is_file():
        assert step.code in ("policies_ready", "index_missing"), step
    else:
        assert step.status == "unavailable" and step.code == "corpus_missing"
        assert "place_private_stores" in step.detail


def test_exit_codes_separate_failed_unavailable_and_ready():
    ok = lc.Step("x", "ok", "c")
    un = lc.Step("y", "unavailable", "c")
    bad = lc.Step("z", "failed", "c")
    assert lc.exit_code([ok, ok]) == 0
    assert lc.exit_code([ok, un]) == 3
    assert lc.exit_code([ok, un, bad]) == 1


def test_run_checks_without_an_engine_is_a_declared_unavailability_not_a_failure():
    steps = lc.run_checks(ROOT, engine="qwen3:14b", base_url="http://127.0.0.1:1", probe=_down,
                          with_ui=False)
    by = {s.step: s for s in steps}
    assert [s.step for s in steps] == ["runtime", "morphology", "engine", "agent_turn", "policies"]
    assert by["runtime"].status == "ok"
    assert by["morphology"].status in ("ok", "unavailable")
    assert by["engine"].code == "engine_unreachable"
    assert by["agent_turn"].code == "mechanism_only", "بلا محرّكٍ تُثبَت الحلقةُ بالمزوّد الآلي"
    assert lc.exit_code(steps) == 3


def test_the_ui_entry_point_initialises():
    assert lc.check_ui(ROOT).status == "ok"
